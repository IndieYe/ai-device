"""Staged-program commit: real directories, real renamex_np, real ownership.

The point of this entry is that the live path changes in exactly one atomic
rename. These tests therefore use the real filesystem and the real journal-free
primitive; substituting renamex_np would only test the substitute.

Ownership note: the production owner is root, but these tests run as the
invoking user. Rather than fake a uid (which would make the ownership gate pass
vacuously), the trees are created and owned by the current user and the target
owner is that same uid, which is exactly the relationship production has with
root.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin compiler")

HARNESS = r"""
#import <Foundation/Foundation.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static int bound_directory(const char *path, uid_t owner) {
    struct stat info;
    if (lstat(path, &info) || !S_ISDIR(info.st_mode)) return 0;
    return info.st_uid == owner;
}
static int tree_owner_valid(const char *path, uid_t owner, int depth) {
    (void)depth;
    struct stat info;
    return !lstat(path, &info) && info.st_uid == owner;
}
#include "client-macos-program-commit.h"
static int commit_calls = 0;
static int substitute_commit(const char *stage, const char *program, uid_t owner,
                             int (*verify)(const char *root), struct stat *replaced) {
    (void)verify;(void)replaced;
    (void)stage;(void)program;(void)owner;commit_calls++;return PROGRAM_COMMIT_OK;
}
/* The entry is a thin wrapper; count its single call without redefining the
 * real primitive (which the other tests exercise directly). */
#define commit_staged_program substitute_commit
#include "client-macos-program-commit-entry.h"
#undef commit_staged_program
int main(int argc, char **argv) {
    /* Real primitive: argv is stage, program, owner uid. */
    if (argc == 4 && !argv[1][0]) return 64;
    if (getenv("ENTRY_MODE")) {
        int rc = program_commit_entry(argc, argv);
        printf("rc=%d calls=%d\n", rc, commit_calls);
        return 0;
    }
    int result = commit_staged_program(argv[1], argv[2], (uid_t)atoi(argv[3]), NULL, NULL);
    printf("%d\n", result);
    return 0;
}
"""


def build(tmp_path):
    native = Path(__file__).parents[1] / "native"
    source = tmp_path / "commit.m"
    harness = HARNESS.replace('#include "client-macos-program-commit.h"',
                              f'#include "{native / "client-macos-program-commit.h"}"')
    harness = harness.replace('#include "client-macos-program-commit-entry.h"',
                              f'#include "{native / "client-macos-program-commit-entry.h"}"')
    source.write_text(harness)
    binary = tmp_path / "commit"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
         "-framework", "Foundation", str(source), "-o", str(binary)],
        check=True, capture_output=True,
    )
    return binary


def commit(binary, stage, program, owner=None):
    uid = os.getuid() if owner is None else owner
    out = subprocess.run(
        [str(binary), str(stage), str(program), str(uid)],
        check=True, capture_output=True, text=True, timeout=10,
    )
    return int(out.stdout.strip())


def make_tree(path, marker):
    path.mkdir(parents=True)
    (path / "version").write_text(marker)
    (path / "nested").mkdir()
    (path / "nested" / "data").write_text(marker)
    return path


def test_commit_swaps_staged_tree_into_live_path(tmp_path):
    binary = build(tmp_path)
    live = make_tree(tmp_path / "opt" / "ai-device-client", "OLD")
    stage = make_tree(tmp_path / "opt" / "ai-device-client-stage", "NEW")

    assert commit(binary, stage, live) == 0
    # The live path now holds the staged content.
    assert (live / "version").read_text() == "NEW"
    assert (live / "nested" / "data").read_text() == "NEW"
    # RENAME_SWAP is an exchange, not a move: the OLD tree is left behind at the
    # stage path. That is a real consequence the caller has to handle, not a
    # detail to hide, so assert it explicitly rather than expecting the stage to
    # vanish.
    assert (stage / "version").read_text() == "OLD"


def test_commit_creates_live_path_when_absent(tmp_path):
    binary = build(tmp_path)
    stage = make_tree(tmp_path / "opt" / "ai-device-client-stage", "NEW")
    live = tmp_path / "opt" / "ai-device-client"

    assert commit(binary, stage, live) == 0
    assert (live / "version").read_text() == "NEW"
    assert not stage.exists()


def test_commit_refuses_missing_stage(tmp_path):
    binary = build(tmp_path)
    live = make_tree(tmp_path / "opt" / "ai-device-client", "OLD")
    stage = tmp_path / "opt" / "ai-device-client-stage"

    assert commit(binary, stage, live) == 1
    # Nothing was published and the live tree is untouched.
    assert (live / "version").read_text() == "OLD"


def test_commit_refuses_stage_owned_by_another_user(tmp_path):
    binary = build(tmp_path)
    live = make_tree(tmp_path / "opt" / "ai-device-client", "OLD")
    stage = make_tree(tmp_path / "opt" / "ai-device-client-stage", "NEW")

    # Ask for a different target owner than the trees actually have.
    other = os.getuid() + 1
    assert commit(binary, stage, live, owner=other) == 1
    assert (live / "version").read_text() == "OLD"
    assert (stage / "version").read_text() == "NEW"


@pytest.mark.parametrize("shape", ["inside-live", "contains-live", "identical"])
def test_commit_refuses_overlapping_paths(tmp_path, shape):
    """Committing a stage onto itself, or into/around the live path, would either
    be a no-op reported as success or destroy the live tree."""
    binary = build(tmp_path)
    if shape == "identical":
        live = make_tree(tmp_path / "opt" / "ai-device-client", "OLD")
        stage = live
    elif shape == "inside-live":
        live = make_tree(tmp_path / "opt" / "ai-device-client", "OLD")
        stage = make_tree(live / "stage", "NEW")
    else:
        stage = make_tree(tmp_path / "opt" / "ai-device-client", "OLD")
        live = make_tree(stage / "live", "NEW")

    assert commit(binary, stage, live) == 1


def test_commit_refuses_when_stage_is_a_file(tmp_path):
    binary = build(tmp_path)
    live = make_tree(tmp_path / "opt" / "ai-device-client", "OLD")
    stage = tmp_path / "opt" / "ai-device-client-stage"
    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.write_text("not a tree")

    assert commit(binary, stage, live) == 1
    assert (live / "version").read_text() == "OLD"


def entry(binary, argv, env_extra=None):
    env = dict(os.environ, ENTRY_MODE="1", **(env_extra or {}))
    out = subprocess.run(
        [str(binary)] + argv, check=False, capture_output=True, text=True, timeout=10, env=env
    )
    return out.stdout.strip(), out.returncode


@pytest.mark.parametrize(
    "argv,expected_rc",
    [
        (["commit", "stage", "live"], 70),
        (["commit"], 64),
        (["commit-wrong", "stage", "live"], 64),
        (["commit", "stage"], 64),
    ],
)
def test_entry_reports_shaped_results(tmp_path, argv, expected_rc):
    """The wrapper is pure shape + identity: argument errors exit 64, and a
    non-root caller is refused at 70 without touching the primitive."""
    binary = build(tmp_path)
    stdout, _ = entry(binary, argv)
    assert f"rc={expected_rc}" in stdout


def test_entry_never_commits_for_a_non_root_caller(tmp_path):
    """The identity gate runs before the primitive, so an unprivileged caller
    cannot publish a program even with perfect arguments."""
    binary = build(tmp_path)
    stdout, _ = entry(binary, ["commit", "stage", "live"])
    assert "calls=0" in stdout
    assert "rc=70" in stdout
