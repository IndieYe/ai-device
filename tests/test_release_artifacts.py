"""Local release staging fails closed without contacting or uploading to PyPI."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_artifacts as release
from test_stage_c_tools import candidate


def test_stage_verified_candidate(tmp_path):
    candidate(tmp_path)
    target = tmp_path / "release"
    release.stage(tmp_path, target)
    assert len(release.check_files(target)) == 6
    assert all(len(list((target / group).iterdir())) == 2 for group in release.GROUPS)


def test_no_stage_stale_report(tmp_path):
    candidate(tmp_path)
    report = tmp_path / "artifacts/delivery-verification.json"
    data = json.loads(report.read_text())
    data["artifacts"] = {}
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        release.stage(tmp_path, tmp_path / "release")
    assert not (tmp_path / "release").exists()


def test_refuse_existing_directory(tmp_path):
    candidate(tmp_path)
    target = tmp_path / "release"
    target.mkdir()
    with pytest.raises(ValueError, match="already exist"):
        release.stage(tmp_path, target)


@pytest.mark.parametrize("change", ["tamper", "missing", "extra"])
def test_check_rejects_changed_files(tmp_path, change):
    candidate(tmp_path)
    target = tmp_path / "release"
    release.stage(tmp_path, target)
    file = next((target / "bash").iterdir())
    if change == "tamper":
        file.write_bytes(b"changed")
    elif change == "missing":
        file.unlink()
    else:
        (target / "bash/extra.whl").write_bytes(b"extra")
    with pytest.raises(ValueError):
        release.check_files(target)


def test_public_metadata_mismatch_stops(tmp_path, monkeypatch):
    import io

    candidate(tmp_path)
    target = tmp_path / "release"
    release.stage(tmp_path, target)
    manifest = release.check_files(target)
    files = [
        {"filename": Path(n).name, "digests": {"sha256": "wrong"}, "yanked": False}
        for n in manifest
        if n.startswith("bash/")
    ]
    monkeypatch.setattr(
        release.urllib.request,
        "urlopen",
        lambda *a, **kw: io.BytesIO(json.dumps({"urls": files}).encode()),
    )
    with pytest.raises(ValueError, match="metadata mismatch"):
        release.check_public(target, "bash")


def test_public_downloads_match(tmp_path, monkeypatch):
    import io

    candidate(tmp_path)
    target = tmp_path / "release"
    release.stage(tmp_path, target)
    manifest = release.check_files(target)
    files = [
        {
            "filename": Path(n).name,
            "digests": {"sha256": digest},
            "yanked": False,
            "url": "https://files.pythonhosted.org/" + Path(n).name,
        }
        for n, digest in manifest.items()
        if n.startswith("bash/")
    ]

    def response(url, **kwargs):
        if url.startswith("https://pypi.org/"):
            return io.BytesIO(json.dumps({"urls": files}).encode())
        return io.BytesIO((target / "bash" / url.rsplit("/", 1)[-1]).read_bytes())

    monkeypatch.setattr(release.urllib.request, "urlopen", response)
    release.check_public(target, "bash")
