"""Render review-only adoption assets. Never install, chmod host roots or run systemctl."""

import argparse
import json
import re
import shutil
from pathlib import Path

FIELDS = {
    "root",
    "client_unit",
    "client_user",
    "client_group",
    "token_file",
    "supervisor_home",
    "supervisor_python",
    "upgrade_id",
    "channel",
    "allowed_versions",
    "bundle_channels",
    "maintenance_windows_utc",
}


def render(config, output):
    if set(config) != FIELDS:
        raise ValueError("explicit complete adoption configuration required")
    # Restrict systemd syntax, specifiers and whitespace rather than pretending
    # shell quoting also quotes systemd unit directives.
    for key in ("root", "token_file", "supervisor_home", "supervisor_python"):
        value = config[key]
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
            or ".." in Path(value).parts
            or str(Path(value)) != value
            or value == "/"
        ):
            raise ValueError(
                "paths must be canonical absolute paths without systemd special syntax"
            )
    root, home = Path(config["root"]), Path(config["supervisor_home"])
    if root == home or root in home.parents or home in root.parents:
        raise ValueError("supervisor must be installed outside the managed instance")
    if home not in Path(config["supervisor_python"]).parents:
        raise ValueError(
            "supervisor interpreter must belong to independent installation"
        )
    token = Path(config["token_file"])
    if root == token or root in token.parents or home in token.parents:
        raise ValueError("credential file must be external to software roots")
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.service", config["client_unit"]):
        raise ValueError("invalid explicit unit name")
    for key in ("client_user", "client_group"):
        if (
            not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", config[key])
            or config[key] == "root"
        ):
            raise ValueError("dedicated non-root client identity required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", config["upgrade_id"]):
        raise ValueError("invalid upgrade ID")
    from device_runtime.upgrade_policy import check_release

    # Validate policy syntax using an allowed exact bundle and a window's start;
    # this is rendering, NOT authorization that the present time is in-window.
    bundles = config["bundle_channels"]
    versions = config["allowed_versions"]
    windows = config["maintenance_windows_utc"]
    if not isinstance(bundles, dict) or not bundles or not versions or not windows:
        raise ValueError("explicit release policy required")
    from datetime import datetime

    matching = [h for h, channel in bundles.items() if channel == config["channel"]]
    if not matching:
        raise ValueError("no approved bundle in local channel")
    check_release(
        config,
        release=versions[0],
        fingerprint=matching[0],
        now=datetime.fromisoformat(windows[0]["start"]),
    )
    output = Path(output)
    output.mkdir(
        mode=0o700
    )  # Refuse overwrite, including existing empty/symlink targets.
    try:
        templates = Path(__file__).resolve().parents[1] / "templates/upgrade-stage1"
        replacements = {
            "@" + key.upper() + "@": value
            for key, value in config.items()
            if isinstance(value, str)
        }
        for name in ("client.service", "supervisor.service"):
            text = (templates / (name + ".in")).read_text()
            for key, value in replacements.items():
                text = text.replace(key, value)
            if re.search(r"@[A-Z_]+@", text):
                raise ValueError("unresolved template field")
            (output / name).write_text(text)
        profile = {
            key: config[key]
            for key in (
                "root",
                "channel",
                "allowed_versions",
                "bundle_channels",
                "maintenance_windows_utc",
            )
        }
        profile.update(
            format_version=1,
            role="client",
            unit=config["client_unit"],
            allow_upgrade=False,
        )
        (output / "upgrade-profile.json").write_text(
            json.dumps(profile, indent=2) + "\n"
        )
        (output / "REVIEW.txt").write_text(
            "NOT INSTALLED. Profile intentionally disabled. No credentials generated.\n"
            "Review docs/upgrade-stage1-adoption.md and upgrade-stage1-recovery.md.\n"
            "Client is filesystem-restricted; this is not transparent legacy adoption.\n"
            "Supervisor runs root: fixed-request only, not a least-privilege privilege broker.\n"
        )
        for path in output.iterdir():
            path.chmod(0o600)
    except BaseException:
        shutil.rmtree(output)
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        render(json.loads(args.config.read_text()), args.output)
    except (ValueError, OSError, KeyError, TypeError):
        parser.exit(
            2,
            "adoption rendering rejected; check explicit configuration and new output directory\n",
        )


if __name__ == "__main__":
    main()
