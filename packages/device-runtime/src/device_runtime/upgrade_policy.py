"""Administrator-owned release allowlist and explicit UTC maintenance windows.

Channels are a local mapping of exact bundle fingerprints, never an untrusted
claim from a request or an inference from version spelling.
"""

from datetime import UTC, datetime

from .upgrader import UpgradeError


def check_release(settings, *, release, fingerprint, now=None):
    channel = settings.get("channel")
    releases = settings.get("allowed_versions")
    bundles = settings.get("bundle_channels")
    windows = settings.get("maintenance_windows_utc")
    if (
        not isinstance(channel, str)
        or not channel
        or not isinstance(releases, list)
        or not releases
        or not all(isinstance(item, str) and item for item in releases)
        or not isinstance(bundles, dict)
        or not isinstance(windows, list)
        or not windows
    ):
        raise UpgradeError("incomplete local release policy")
    if release not in releases or bundles.get(fingerprint) != channel:
        raise UpgradeError("release or channel not allowed by local policy")
    instant = datetime.now(UTC) if now is None else now
    allowed = False
    for window in windows:
        if not isinstance(window, dict) or set(window) != {"start", "end"}:
            raise UpgradeError("invalid local maintenance window")
        try:
            start = datetime.fromisoformat(window["start"])
            end = datetime.fromisoformat(window["end"])
        except (TypeError, ValueError):
            raise UpgradeError("invalid local maintenance window") from None
        if (
            start.utcoffset() is None
            or end.utcoffset() is None
            or start.utcoffset().total_seconds() != 0
            or end.utcoffset().total_seconds() != 0
            or start >= end
        ):
            raise UpgradeError("maintenance window must be an increasing UTC interval")
        allowed = allowed or start <= instant < end
    if not allowed:
        raise UpgradeError("outside local maintenance window")
