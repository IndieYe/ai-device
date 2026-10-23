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


@pytest.mark.parametrize("group", release.GROUPS)
@pytest.mark.parametrize("attestation_count", [0, 1, 2])
def test_public_downloads_match(tmp_path, monkeypatch, group, attestation_count):
    import io

    candidate(tmp_path)
    target = tmp_path / "release"
    release.stage(tmp_path, target)
    manifest = release.check_files(target)
    if attestation_count:
        names = [name for name in manifest if name.startswith(group + "/")]
        for name in names[:attestation_count]:
            (target / (name + ".publish.attestation")).write_text("proof")
        with pytest.raises(ValueError, match="file set mismatch"):
            release.check_files(target)
    files = [
        {
            "filename": Path(n).name,
            "digests": {"sha256": digest},
            "yanked": False,
            "url": "https://files.pythonhosted.org/" + Path(n).name,
        }
        for n, digest in manifest.items()
        if n.startswith(group + "/")
    ]
    requests = []

    def response(url, **kwargs):
        requests.append(url)
        if url.startswith("https://pypi.org/"):
            return io.BytesIO(json.dumps({"urls": files}).encode())
        return io.BytesIO((target / group / url.rsplit("/", 1)[-1]).read_bytes())

    monkeypatch.setattr(release.urllib.request, "urlopen", response)
    release.check_public(target, group)
    assert len(requests) == 3


@pytest.mark.parametrize(
    "change",
    ["extra", "orphan", "other-group", "directory", "symlink", "missing", "tamper"],
)
def test_public_sidecars_do_not_relax_candidate_checks(tmp_path, monkeypatch, change):
    candidate(tmp_path)
    target = tmp_path / "release"
    release.stage(tmp_path, target)
    manifest = release.check_files(target)
    artifact = next(target / n for n in manifest if n.startswith("bash/"))
    sidecar = artifact.with_name(artifact.name + ".publish.attestation")
    if change == "directory":
        sidecar.mkdir()
    elif change == "symlink":
        sidecar.symlink_to(artifact.name)
    else:
        sidecar.write_text("proof")
        if change == "extra":
            (target / "bash/extra.whl").write_bytes(b"extra")
        elif change == "orphan":
            (target / "bash/unknown.whl.publish.attestation").write_text("proof")
        elif change == "other-group":
            other = next(n for n in manifest if n.startswith("fs/"))
            (target / (other + ".publish.attestation")).write_text("proof")
        elif change == "missing":
            artifact.unlink()
        elif change == "tamper":
            artifact.write_bytes(b"changed")

    def unexpected_network(*args, **kwargs):
        pytest.fail("invalid local candidate must fail before public lookup")

    monkeypatch.setattr(release.urllib.request, "urlopen", unexpected_network)
    with pytest.raises(ValueError):
        release.check_public(target, "bash")


def test_public_sidecars_do_not_hide_download_mismatch(tmp_path, monkeypatch):
    import io

    candidate(tmp_path)
    target = tmp_path / "release"
    release.stage(tmp_path, target)
    manifest = release.check_files(target)
    files = []
    for name, digest in manifest.items():
        if name.startswith("bash/"):
            (target / (name + ".publish.attestation")).write_text("proof")
            files.append(
                {
                    "filename": Path(name).name,
                    "digests": {"sha256": digest},
                    "yanked": False,
                    "url": "https://files.pythonhosted.org/" + Path(name).name,
                }
            )

    def response(url, **kwargs):
        if url.startswith("https://pypi.org/"):
            return io.BytesIO(json.dumps({"urls": files}).encode())
        return io.BytesIO(b"wrong public bytes")

    monkeypatch.setattr(release.urllib.request, "urlopen", response)
    with pytest.raises(ValueError, match="download mismatch"):
        release.check_public(target, "bash")
