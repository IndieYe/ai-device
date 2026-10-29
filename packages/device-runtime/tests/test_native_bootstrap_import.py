"""The supervisor bootstrap must import with only its own shipped modules.

The packaged helper runs from a minimal runtime that contains just
``__init__.py``, ``entrypoint.py``, ``native_helper.py``,
``management_lock.py`` and ``native_bootstrap.py``. Importing anything heavier
from the helper makes the installed ``ai-device-native-helper`` unimportable,
which silently removes every supervisor-side lifecycle gate.
"""

import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "device_runtime"

BOOTSTRAP_MODULES = [
    "__init__.py",
    "entrypoint.py",
    "native_helper.py",
    "management_lock.py",
    "native_bootstrap.py",
]


def test_bootstrap_module_set_imports_standalone(tmp_path):
    site = tmp_path / "site"
    package = site / "device_runtime"
    package.mkdir(parents=True)
    for name in BOOTSTRAP_MODULES:
        shutil.copy(PACKAGE / name, package / name)

    code = "\n".join(
        [
            f"import sys; sys.path.insert(0, {str(site)!r})",
            "import device_runtime.native_helper as helper",
            "import device_runtime.native_bootstrap as bootstrap",
            "assert helper.LAUNCHER.startswith('#!/bin/sh')",
            "assert helper.RUNNER_BODY",
            "assert helper.POINTER",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_entrypoint_text_matches_installation(tmp_path):
    code = "\n".join(
        [
            f"import sys; sys.path.insert(0, {str(PACKAGE.parent)!r})",
            "from device_runtime.entrypoint import LAUNCHER, RUNNER_BODY, POINTER",
            "from device_runtime.installation import LAUNCHER as L2, RUNNER_BODY as B2",
            "assert LAUNCHER == L2",
            "assert RUNNER_BODY == B2",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
