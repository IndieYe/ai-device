import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/build-client-tree.py"
spec = importlib.util.spec_from_file_location("client_tree_build", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_exact_application_source_verification(tmp_path):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / "application.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example/__init__.py", "VERSION = 2\n")
    site = tmp_path / "site"
    (site / "example").mkdir(parents=True)
    installed = site / "example/__init__.py"
    installed.write_text("VERSION = 1\n")
    with pytest.raises(ValueError, match="differs"):
        module.verify_installed_wheels(wheels, site)
    installed.write_text("VERSION = 2\n")
    records = module.verify_installed_wheels(wheels, site)
    assert records == [
        {"name": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
    ]


def test_input_hash_mismatch(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="mismatch"):
        module.verify(path, {"size": 5, "sha256": "0" * 64})


def test_relocate_python_scripts_preserves_mode_and_binary(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    script = binaries / "device-runtime"
    script.write_bytes(b"#!/tmp/build/runtime/bin/python3\nprint('ok')\n")
    script.chmod(0o755)
    binary = binaries / "python3"
    binary.write_bytes(b"\x7fELFpython")
    link = binaries / "python"
    link.symlink_to("python3")
    module.relocate_python_scripts(tmp_path)
    assert script.read_bytes() == b"#!/opt/ai-device-client/runtime/bin/python3\nprint('ok')\n"
    assert script.stat().st_mode & 0o777 == 0o755
    assert binary.read_bytes() == b"\x7fELFpython"
    assert link.is_symlink()
