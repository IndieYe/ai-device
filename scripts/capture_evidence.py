"""Capture a local, immutable-content candidate inventory; never uploads or publishes."""

import hashlib
import json
import platform
import subprocess
import sys
import zipfile
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {"ai-device", "ai-device-bash", "ai-device-fs"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    evidence = ROOT / "docs/evidence"
    evidence.mkdir(exist_ok=True)
    inventory = []
    notices = [
        "# Third-party dependency notices\n\nLocal development and verification environment inventory, not a universal dependency graph or legal opinion.\n"
    ]
    for distribution in sorted(
        metadata.distributions(), key=lambda d: d.metadata["Name"].lower()
    ):
        name = distribution.metadata["Name"]
        if name in PROJECTS:
            continue
        expression = distribution.metadata.get(
            "License-Expression"
        ) or distribution.metadata.get("License")
        classifiers = [
            v
            for v in distribution.metadata.get_all("Classifier", [])
            if "License ::" in v
        ]
        license_files = []
        for file in distribution.files or []:
            if any(
                part.lower().startswith(("license", "copying", "notice"))
                for part in Path(str(file)).parts
            ):
                path = distribution.locate_file(file)
                if path.is_file():
                    content = path.read_text(errors="replace")
                    license_files.append({"path": str(file), "sha256": digest(path)})
                    notices.append(
                        f"\n## {name} {distribution.version}: {file}\n\n```text\n{content}\n```\n"
                    )
        inventory.append(
            {
                "name": name,
                "version": distribution.version,
                "license_expression_or_text": expression,
                "license_classifiers": classifiers,
                "license_files": license_files,
                "project_urls": distribution.metadata.get_all("Project-URL", []),
                "requires_dist": distribution.requires or [],
            }
        )
        assert expression or classifiers or license_files, (
            f"Missing license evidence: {name}"
        )
    (evidence / "third-party-inventory.json").write_text(
        json.dumps(inventory, indent=2) + "\n"
    )
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text("".join(notices))
    wheel_sources = {}
    artifacts = []
    for path in sorted((ROOT / "dist").iterdir()):
        if path.suffix not in (".whl", ".gz"):
            continue
        artifacts.append(
            {"file": path.name, "sha256": digest(path), "size": path.stat().st_size}
        )
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as wheel:
                entries = [n for n in wheel.namelist() if n.endswith(".py")]
                matching = []
                for entry in entries:
                    candidates = [
                        p
                        for p in (ROOT / "packages").rglob(Path(entry).name)
                        if p.as_posix().endswith("/" + entry)
                        and "__pycache__" not in p.parts
                    ]
                    assert len(candidates) == 1, (entry, candidates)
                    source = candidates[0]
                    assert source.read_bytes() == wheel.read(entry), entry
                    matching.append(
                        {
                            "wheel_path": entry,
                            "source_path": source.relative_to(ROOT).as_posix(),
                            "sha256": digest(source),
                        }
                    )
                assert any("license" in n.lower() for n in wheel.namelist()), path
                wheel_sources[path.name] = matching
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    source_files = {
        name: digest(ROOT / name)
        for name in tracked
        if name and name.startswith("packages/") and (ROOT / name).is_file()
    }
    result = {
        "kind": "local_candidate_not_published",
        "source_commit_before_evidence": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "identity_note": "Source file hashes, lock and artifact hashes define this candidate; commit predates evidence and may have working-tree changes. Device build_id excludes Bash/FS.",
        "python": sys.version,
        "platform": platform.platform(),
        "uv_lock_sha256": digest(ROOT / "uv.lock"),
        "artifacts": artifacts,
        "wheel_python_source_matches": wheel_sources,
        "package_source_files": source_files,
        "verified_resolution": json.loads(
            (ROOT / "artifacts/delivery-verification.json").read_text()
        )["minimal_roles"],
        "stage_c_limitations": [
            "macOS arm64 real lifecycle only",
            "Linux CI configured but not run",
            "No production or host migration",
            "Third-party inventory is not comprehensive provenance/legal audit",
        ],
    }
    (evidence / "candidate-manifest.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    for name in [
        "delivery-verification.json",
        "artifact-verification.json",
        "collection-comparison.json",
    ]:
        (evidence / name).write_bytes((ROOT / "artifacts" / name).read_bytes())
    print(
        f"Captured {len(artifacts)} artifacts, source matching for {len(wheel_sources)} wheels, {len(inventory)} dependency licenses"
    )


if __name__ == "__main__":
    main()
