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


def validate_candidate(root):
    import ast
    import tomllib

    expected = set()
    for package in ("bash", "fs", "device-runtime"):
        project = tomllib.loads(
            (root / "packages" / package / "pyproject.toml").read_text()
        )["project"]
        name = project["name"].replace("-", "_")
        version = project.get("version")
        if version is None:
            tree = ast.parse(
                (
                    root / "packages/device-runtime/src/device_runtime/__init__.py"
                ).read_text()
            )
            version = next(
                ast.literal_eval(n.value)
                for n in tree.body
                if isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "__version__" for t in n.targets
                )
            )
        expected.update(
            (f"{name}-{version}.tar.gz", f"{name}-{version}-py3-none-any.whl")
        )
    actual = {p.name for p in (root / "dist").iterdir() if p.suffix in (".whl", ".gz")}
    if actual != expected:
        raise ValueError("candidate requires exactly three sdist/wheel pairs")
    hashes = {name: digest(root / "dist" / name) for name in expected}
    delivery = json.loads((root / "artifacts/delivery-verification.json").read_text())
    artifact = json.loads((root / "artifacts/artifact-verification.json").read_text())
    if delivery["artifacts"] != hashes:
        raise ValueError("delivery report does not match candidate")
    if artifact["input_artifacts"] != hashes:
        raise ValueError("artifact report does not match candidate")
    if artifact["rebuilt_wheels"] != {
        n: h for n, h in hashes.items() if n.endswith(".whl")
    }:
        raise ValueError("rebuilt wheels differ from candidate")
    if (
        set(delivery["minimal_roles"]) != {"client", "center", "mcp", "ops"}
        or set(delivery["lifecycle"])
        != {
            "install_and_inspect",
            "stable_runner_center_http",
            "stable_runner_client_and_mcp",
            "running_lease_rejected",
            "prepare_activate_rebuild",
            "rollback_backup_restore",
            "invalid_release_keeps_active",
            "linux_target_role_rejected",
            "tampered_wheel_keeps_active",
        }
        or not all(v is True for v in delivery["lifecycle"].values())
    ):
        raise ValueError("incomplete or failed delivery report")
    if not artifact.get("scope") or not artifact.get("python"):
        raise ValueError("incomplete artifact report")
    return hashes


def notice_header():
    return (
        "# Third-party dependency notices\n\n"
        "Local development and verification environment inventory, "
        "not a universal dependency graph or legal opinion.\n\n"
        "Version-specific supplemental license texts and redistribution scope: "
        "[third-party license materials](docs/third-party-licenses/README.md) "
        "([index](docs/third-party-licenses/index.json)). "
        "These repository materials are not automatically included in bundles.\n"
    )


def main():
    evidence = ROOT / "docs/evidence"
    validate_candidate(ROOT)
    pending = {}
    inventory = []
    notices = [notice_header()]
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
    pending[evidence / "third-party-inventory.json"] = (
        json.dumps(inventory, indent=2) + "\n"
    )
    pending[ROOT / "THIRD_PARTY_NOTICES.md"] = "".join(notices)
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
    pending[evidence / "candidate-manifest.json"] = json.dumps(result, indent=2) + "\n"
    for name in [
        "delivery-verification.json",
        "artifact-verification.json",
        "collection-comparison.json",
    ]:
        pending[evidence / name] = (ROOT / "artifacts" / name).read_text()
    validate_candidate(ROOT)
    evidence.mkdir(exist_ok=True)
    for path, content in pending.items():
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content)
        temporary.replace(path)
    print(
        f"Captured {len(artifacts)} artifacts, source matching for {len(wheel_sources)} wheels, {len(inventory)} dependency licenses"
    )


if __name__ == "__main__":
    main()
