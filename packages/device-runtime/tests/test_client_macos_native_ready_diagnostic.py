"""A failed upgrade must say why it failed, not just that it did.

The coordinator reported LOADED_NOT_READY for every give-up, so a job that never
started and one serving the wrong bytes were indistinguishable in the log. On a
signed package that cost hours: the log held a marker and neither digest.

The wording lives in client-macos-ready-report.h so it can be exercised without
a launchd job, a swap, a reload and 120 seconds of polling -- none of which are
involved in choosing the words. These tests compile the real header.
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin native source")

HARNESS = r"""
#import <Foundation/Foundation.h>
#include <stdio.h>
#include "client-macos-ready-report.h"
int main(int argc,char **argv){@autoreleasepool{
    (void)argc;(void)argv;
    upgrade_report_not_ready(READY_VERSION_MISMATCH,1000,
        @"0.3.6",@"sha256:29b9eb64353205f9d1c2c5ac280a0e363133d5ab6f06364727d307b8ec6a63aa");
    return 0;}}
"""


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    native = Path(__file__).parents[1] / "native"
    build = tmp_path_factory.mktemp("report")
    source = build / "report.m"
    source.write_text(HARNESS)
    binary = build / "report"
    result = subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
         "-framework", "Foundation", "-I", str(native), str(source), "-o", str(binary)],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return subprocess.run([str(binary)], check=False, capture_output=True, text=True, timeout=5)


def test_the_give_up_names_the_cause(report):
    """Without the cause the marker is all an operator gets, which is what failed."""
    assert "upgrade not ready" in report.stderr
    assert "runtime_version mismatch" in report.stderr


def test_the_give_up_names_the_expected_identity(report):
    """The expected digest is the half of the comparison that was invisible.

    Printing only "mismatch" leaves the reader unable to tell which side was
    wrong without querying the device again.
    """
    assert "expected version 0.3.6" in report.stderr
    assert "29b9eb64" in report.stderr


def test_it_reports_once_not_per_poll(report):
    """One line per give-up: the loop polls at 20 Hz for up to 120 seconds."""
    assert report.stderr.count("upgrade not ready") == 1


def test_every_cause_has_a_distinct_printable_name():
    """A new enum case must not silently print the generic fallback."""
    header = (Path(__file__).parents[1] / "native/client-macos-ready-cause.h").read_text()
    for case, text in {
        "READY_NO_RESPONSE": "no valid response",
        "READY_REPORTED_NOT_READY": "client reported ready=false",
        "READY_NONCE_MISMATCH": "response nonce mismatch",
        "READY_PID_MISMATCH": "response pid mismatch",
        "READY_VERSION_MISMATCH": "runtime_version mismatch",
        "READY_PROCESS_CHANGED": "process changed during the query",
    }.items():
        assert case in header, f"{case} missing from the enum"
        assert text in header, f'{case} has no printable name "{text}"'
