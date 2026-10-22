"""Offline checks for supplemental texts, identity and generated-notice links."""

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import capture_evidence as evidence

EXPECTED = {"dnspython": "2.9.0", "fastmcp-slim": "3.4.11", "certifi": "2026.10.19"}


def test_supplemental_materials_match_index():
    directory = ROOT / "docs/third-party-licenses"
    data = json.loads((directory / "index.json").read_text())
    assert data["schema_version"] == 1
    assert {entry["name"]: entry["version"] for entry in data["entries"]} == EXPECTED
    inventory = {
        entry["name"]: entry
        for entry in json.loads(
            (ROOT / "docs/evidence/third-party-inventory.json").read_text()
        )
    }
    paths = set()
    for entry in data["entries"]:
        assert inventory[entry["name"]]["version"] == entry["version"]
        assert re.fullmatch(r"[0-9a-f]{40}", entry["source_commit"])
        assert entry["source_commit"] in entry["source_archive_url"]
        assert re.fullmatch(r"[0-9a-f]{64}", entry["wheel"]["sha256"])
        assert entry["wheel"]["url"].startswith("https://files.pythonhosted.org/")
        for material in entry["license_materials"]:
            path = directory / material["path"]
            assert path.resolve().is_relative_to(directory.resolve())
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == material["sha256"]
            assert material["source_url"].startswith("https://")
            if path.name == "LICENSE":
                assert entry["source_commit"] in material["source_url"]
            paths.add(path.relative_to(directory).as_posix())
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.name not in {"README.md", "index.json"}
    }
    assert actual == paths


def test_generated_and_existing_notice_link_to_independent_materials():
    header = evidence.notice_header()
    notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    assert notice.startswith(header)
    assert "not automatically included in bundles" in header
    for relative in [
        "docs/third-party-licenses/README.md",
        "docs/third-party-licenses/index.json",
    ]:
        assert relative in header
        assert (ROOT / relative).is_file()


def test_readme_relative_links_resolve():
    directory = ROOT / "docs/third-party-licenses"
    text = (directory / "README.md").read_text()
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if not target.startswith("https://"):
            assert (directory / target).is_file(), target
