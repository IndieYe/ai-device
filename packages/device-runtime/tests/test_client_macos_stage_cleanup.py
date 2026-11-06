"""Stage cleanup: what gets removed after a swap, and what must never be.

The dangerous failure mode here is not "cleanup did not happen" but "cleanup
deleted the live program". Every test below is written with that in mind, so the
refusal cases assert the live tree still exists and still holds the NEW content.

Real directories and the real rename/unlink path are used; the ownership gate is
exercised with the current uid, which is the same relationship production has
with root.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin compiler")

HARNESS = r"""
#import <Foundation/Foundation.h>
#include <dirent.h>
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
#include "client-macos-stage-cleanup.h"
int main(int argc, char **argv) {
    if (argc != 4) return 64;
    int result = cleanup_swapped_stage(argv[1], argv[2], (uid_t)atoi(argv[3]));
    printf("%d\n", result);
    return 0;
}
"""


def build(tmp_path):
    native = Path(__file__).parents[1] / "native"
    source = tmp_path / "cleanup.m"
    source.write_text(
        HARNESS.replace('#include "client-macos-stage-cleanup.h"',
                        f'#include "{native / "client-macos-stage-cleanup.h"}"')
    )
    binary = tmp_path / "cleanup"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
         "-framework", "Foundation", str(source), "-o", str(binary)],
        check=True, capture_output=True,
    )
    return binary


def run(binary, stage, program, owner=None):
    uid = os.getuid() if owner is None else owner
    out = subprocess.run(
        [str(binary), str(stage), str(program), str(uid)],
        check=True, capture_output=True, text=True, timeout=15,
    )
    return int(out.stdout.strip())


def make_tree(path, marker, depth=2):
    path.mkdir(parents=True)
    (path / "version").write_text(marker)
    nested = path
    for index in range(depth):
        nested = nested / f"d{index}"
        nested.mkdir()
    (nested / "leaf").write_text(marker)
    return path


def test_cleanup_removes_the_swapped_out_old_tree(tmp_path):
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    stage = make_tree(tmp_path / "opt" / "ai-device-client-stage", "OLD")

    assert run(binary, stage, program) == 0
    # The old tree is gone, including nested content.
    assert not stage.exists()
    # The live program is untouched.
    assert (program / "version").read_text() == "NEW"
    # No pid-tagged debris left behind.
    assert not list((tmp_path / "opt").glob("ai-device-client-stage.old-*"))


def test_cleanup_refuses_a_symlinked_stage_with_a_trailing_slash(tmp_path):
    """A trailing slash defeats the symlink check entirely.

    `lstat("link/")` resolves the final component as a directory, so it reports
    S_ISDIR and never S_ISLNK — the guard ran on the link TARGET. Verified against
    the previous revision: with a stage symlink pointing at an unrelated tree,
    passing `stage/` made cleanup rename and recursively delete that tree and
    return success. This test is the regression for that exact bypass.
    """
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    victim = make_tree(tmp_path / "victim", "PRECIOUS")
    stage = tmp_path / "opt" / "ai-device-client-stage"
    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.symlink_to(victim, target_is_directory=True)

    assert run(binary, f"{stage}/", program) == 1
    # The whole point: the link target is still there, contents intact.
    assert (victim / "version").read_text() == "PRECIOUS"
    assert (program / "version").read_text() == "NEW"
    assert stage.is_symlink()


def test_cleanup_refuses_a_trailing_slash_even_without_a_symlink(tmp_path):
    """The slash is rejected outright rather than normalised, so the refusal does
    not depend on what kind of object happens to sit at that name.

    Normalising would be the tempting fix, but it means accepting a spelling whose
    symlink test cannot be trusted, and the two behaviours must not differ.
    """
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    stage = make_tree(tmp_path / "opt" / "ai-device-client-stage", "OLD")

    assert run(binary, f"{stage}/", program) == 1
    assert (stage / "version").read_text() == "OLD"


def test_cleanup_refuses_and_never_follows_a_symlinked_stage(tmp_path):
    """A symlinked stage could redirect the delete to any directory."""
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    victim = make_tree(tmp_path / "victim", "PRECIOUS")
    stage = tmp_path / "opt" / "ai-device-client-stage"
    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.symlink_to(victim)

    assert run(binary, stage, program) == 1
    # The symlink target survives intact.
    assert (victim / "version").read_text() == "PRECIOUS"
    assert (program / "version").read_text() == "NEW"


@pytest.mark.parametrize("shape", ["identical", "stage-contains-program", "program-contains-stage"])
def test_cleanup_refuses_paths_overlapping_the_live_program(tmp_path, shape):
    """This function deletes a whole tree; it must be incapable of deleting the
    program it just installed."""
    binary = build(tmp_path)
    if shape == "identical":
        program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
        stage = program
    elif shape == "stage-contains-program":
        stage = make_tree(tmp_path / "opt" / "stage", "X")
        program = make_tree(stage / "live", "NEW")
    else:
        program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
        stage = make_tree(program / "inner", "X")

    assert run(binary, stage, program) == 1
    assert (program / "version").read_text() == "NEW"


def test_cleanup_refuses_when_stage_is_absent(tmp_path):
    """Absent is not an error to retry, and it must not be treated as success:
    the caller needs to know there was nothing to remove."""
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    stage = tmp_path / "opt" / "ai-device-client-stage"

    assert run(binary, stage, program) == 1
    assert (program / "version").read_text() == "NEW"


def test_cleanup_refuses_a_stage_owned_by_another_user(tmp_path):
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    stage = make_tree(tmp_path / "opt" / "ai-device-client-stage", "OLD")

    assert run(binary, stage, program, owner=os.getuid() + 1) == 1
    assert (stage / "version").read_text() == "OLD"


def test_cleanup_leaves_foreign_debris_untouched(tmp_path):
    """Debris is pid-tagged, so a leftover from an EARLIER run does not block a
    new cleanup: only this process's own name can collide, which cannot happen.

    Asserted explicitly because it is a real limitation, not a guarantee: debris
    from interrupted cleanups is not reclaimed automatically and will accumulate
    until something else removes it. The rename uses RENAME_EXCL, so if a
    collision ever did occur the cleanup refuses instead of overwriting.
    """
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    stage = make_tree(tmp_path / "opt" / "ai-device-client-stage", "OLD")
    # pid 1 will not be this subprocess.
    foreign = make_tree(tmp_path / "opt" / "ai-device-client-stage.old-1", "DEBRIS")

    assert run(binary, stage, program) == 0
    assert not stage.exists()
    assert (program / "version").read_text() == "NEW"
    # The foreign debris is still there: cleanup only ever touches its own name.
    assert (foreign / "version").read_text() == "DEBRIS"


@pytest.mark.parametrize(
    "alias",
    [
        ".",  # /opt/./ai-device-client
        "../opt",  # /opt/../opt/ai-device-client
        "",  # trailing slash: /opt/ai-device-client/
    ],
)
def test_cleanup_refuses_a_path_alias_of_the_live_program(tmp_path, alias):
    """A string comparison cannot see that `/opt/x` and `/opt/./x` are the same
    directory, so the previous gate accepted a stage that WAS the live tree and
    then recursed into it.

    These are the exact bypasses the independent review found. If any of them
    passes, cleanup deletes the program it just installed.
    """
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    if alias == "":
        stage = f"{program}/"
    else:
        stage = f"{program.parent}/{alias}/ai-device-client"

    assert run(binary, stage, program) == 1
    # The whole point: the live tree survives, contents intact.
    assert (program / "version").read_text() == "NEW"
    assert (program / "d0" / "d1" / "leaf").read_text() == "NEW"


def test_cleanup_refuses_a_stage_reached_through_a_symlinked_parent(tmp_path):
    """A symlinked parent makes two different strings address one tree, which no
    textual overlap check can detect. The stage here is a real sibling directory
    of the live tree when resolved, so only identity comparison catches it."""
    binary = build(tmp_path)
    opt = tmp_path / "opt"
    program = make_tree(opt / "ai-device-client", "NEW")
    (tmp_path / "link").symlink_to(opt, target_is_directory=True)
    stage = tmp_path / "link" / "ai-device-client"

    assert run(binary, stage, program) == 1
    assert (program / "version").read_text() == "NEW"


def test_cleanup_refuses_when_the_stage_cannot_be_resolved(tmp_path):
    """An unresolvable path is not evidence of safety. A dangling symlink must be
    refused, not silently treated as "different from the program"."""
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    stage = tmp_path / "opt" / "ai-device-client-stage"
    stage.symlink_to(tmp_path / "opt" / "missing-target")

    assert run(binary, stage, program) == 1
    assert (program / "version").read_text() == "NEW"


BIND_HARNESS = r"""
#import <Foundation/Foundation.h>
#include <errno.h>
#include <fcntl.h>
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
#include "client-macos-stage-cleanup.h"
/* argv: parent_dir name expected_dev expected_ino owner */
int main(int argc, char **argv) {
    (void)argc;
    struct stat expected;
    expected.st_dev = (dev_t)atoll(argv[3]);
    expected.st_ino = (ino_t)atoll(argv[4]);
    int fd = open(argv[1], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) { printf("open-failed\n"); return 0; }
    int rc = stage_entry_bound(fd, argv[2], &expected, (uid_t)atoi(argv[5]));
    printf("%d\n", rc);
    close(fd);
    return 0;
}
"""


def build_bind_harness(tmp_path):
    native = Path(__file__).parents[1] / "native"
    source = tmp_path / "bind.m"
    source.write_text(
        BIND_HARNESS.replace(
            '#include "client-macos-stage-cleanup.h"',
            f'#include "{native / "client-macos-stage-cleanup.h"}"',
        )
    )
    binary = tmp_path / "bind"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
         "-framework", "Foundation", str(source), "-o", str(binary)],
        check=True, capture_output=True,
    )
    return binary


def test_binding_rejects_a_different_inode_than_the_one_validated(tmp_path):
    """The check/use binding is the part a path string cannot express.

    A concurrent actor that replaces the stage entry between validation and
    rename leaves the name valid and the object different. Feeding the predicate
    the WRONG expected inode is exactly that state, and it must be refused —
    otherwise the earlier validation would authorize deleting something else.
    """
    binary = build_bind_harness(tmp_path)
    parent = tmp_path / "opt"
    stage = make_tree(parent / "ai-device-client-stage", "OLD")
    other = make_tree(parent / "other", "OTHER")
    info = os.lstat(stage)
    uid = os.getuid()

    def bind(name, dev, ino):
        out = subprocess.run(
            [str(binary), str(parent), name, str(dev), str(ino), str(uid)],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip()

    # Same object as validated: bound.
    assert bind("ai-device-client-stage", info.st_dev, info.st_ino) == "1"
    # A different, equally valid directory: refused.
    assert bind("other", info.st_dev, info.st_ino) == "0"
    # A name that does not exist at all: refused.
    assert bind("missing", info.st_dev, info.st_ino) == "0"
    other_info = os.lstat(other)
    assert bind("other", other_info.st_dev, other_info.st_ino) == "1"


def test_binding_refuses_a_symlink_even_when_its_own_inode_matches(tmp_path):
    """A symlink at the entry must be refused on its own account.

    The expected inode passed here is the LINK's own inode, so an inode comparison
    performed without the type check would pass. Both checks are required.
    (An earlier name said "matching target inode", which was wrong: the target is
    deliberately different, only the link's own inode matches.)
    """
    binary = build_bind_harness(tmp_path)
    parent = tmp_path / "opt"
    target = make_tree(parent / "target", "T")
    link = parent / "link"
    link.symlink_to(target, target_is_directory=True)
    link_info = os.lstat(link)

    out = subprocess.run(
        [str(binary), str(parent), "link", str(link_info.st_dev), str(link_info.st_ino),
         str(os.getuid())],
        check=True, capture_output=True, text=True, timeout=10,
    )
    assert out.stdout.strip() == "0"
    assert (target / "version").read_text() == "T"


@pytest.mark.parametrize(
    "spelling",
    [
        "{stage}/",
        "{stage}//",
        "{stage}/.",
        "{stage}/..",
        "{parent}/./{name}",
        "{stage}/./inner",
        "{parent}//{name}",
    ],
)
def test_cleanup_refuses_any_spelling_that_crosses_a_link(tmp_path, spelling):
    """Every spelling that makes the kernel resolve through the stage link.

    This replaced a one-at-a-time approach that kept losing. Rejecting `link/`
    closed that literal form, then `link/.` deleted the link target just the
    same — the kernel resolves `.`, `..` and empty components AFTER crossing a
    link, so `lstat("link/.")` reports the TARGET and the symlink test inspects
    the wrong object. Both were reproduced against the previous revisions (target
    tree deleted, exit 0).

    The contract is now "plain absolute path only", checked as a whole spelling.
    """
    binary = build(tmp_path)
    parent = tmp_path / "opt"
    program = make_tree(parent / "ai-device-client", "NEW")
    victim = make_tree(tmp_path / "victim", "PRECIOUS")
    link = parent / "stage-link"
    parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(victim, target_is_directory=True)

    stage = spelling.format(stage=link, parent=parent, name="stage-link")
    assert run(binary, stage, program) == 1
    # The whole point: the link target survives, every time.
    assert (victim / "version").read_text() == "PRECIOUS"
    assert (program / "version").read_text() == "NEW"


def test_cleanup_still_removes_a_plain_stage_path(tmp_path):
    """The stricter spelling contract must not break the real use case."""
    binary = build(tmp_path)
    program = make_tree(tmp_path / "opt" / "ai-device-client", "NEW")
    stage = make_tree(tmp_path / "opt" / "ai-device-client-stage", "OLD")

    assert run(binary, stage, program) == 0
    assert not stage.exists()
    assert (program / "version").read_text() == "NEW"


def test_production_cleanup_verb_is_disabled(tmp_path):
    """The `cleanup` verb refuses unconditionally, by decision.

    Four review rounds produced four ways to reach a directory that was not the
    swapped-out old tree: `/opt/./x`, `link/`, `link/.`, and finally a plain
    absolute path whose MIDDLE component is a link — which no spelling check can
    exclude. Each fix closed one spelling, so the entry itself was withdrawn.

    The primitive stays compiled and tested (its invariants are still worth
    holding), but nothing may delete a program tree from two argv paths until the
    coordinator drives cleanup with a commit receipt and the old tree's identity.

    Asserted at the source level because the verb is only reachable as root, and
    the binary cannot be exercised as root here. The exact refusal is checked so a
    future re-enable has to consciously remove this test.
    """
    source = (Path(__file__).parents[1] / "native" / "client-macos-commit-main.c").read_text()
    assert "STAGE_CLEANUP_DISABLED_V1" in source
    # The disabled branch must not call the primitive any more.
    disabled = source.split("if (is_cleanup)")[1].split("switch (commit_staged_program")[0]
    assert "cleanup_swapped_stage" not in disabled
    assert "return 64" in disabled
