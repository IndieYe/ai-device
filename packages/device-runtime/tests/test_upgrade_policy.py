from datetime import UTC, datetime

import pytest

from device_runtime.upgrade_policy import check_release
from device_runtime.upgrader import UpgradeError


def settings():
    return {
        "channel": "stable",
        "allowed_versions": ["0.2.2"],
        "bundle_channels": {"exact-bundle-hash": "stable"},
        "maintenance_windows_utc": [
            {"start": "2026-10-23T10:00:00+00:00", "end": "2026-10-23T11:00:00+00:00"}
        ],
    }


def verify(value, **kwargs):
    check_release(value, release="0.2.2", fingerprint="exact-bundle-hash", **kwargs)


@pytest.mark.parametrize("hour,allowed", [(9, False), (10, True), (11, False)])
def test_explicit_utc_interval(hour, allowed):
    now = datetime(2026, 10, 23, hour, tzinfo=UTC)
    if allowed:
        verify(settings(), now=now)
    else:
        with pytest.raises(UpgradeError, match="outside"):
            verify(settings(), now=now)


@pytest.mark.parametrize(
    "key,value",
    [
        ("channel", "preview"),
        ("allowed_versions", ["0.2.1"]),
        ("bundle_channels", {"other-bundle": "stable"}),
    ],
)
def test_local_allowlist_not_request_claim(key, value):
    policy = settings()
    policy[key] = value
    with pytest.raises(UpgradeError, match="not allowed"):
        verify(policy)


@pytest.mark.parametrize(
    "key", ["channel", "allowed_versions", "bundle_channels", "maintenance_windows_utc"]
)
def test_missing_policy_fails_closed(key):
    policy = settings()
    policy.pop(key)
    with pytest.raises(UpgradeError, match="incomplete"):
        verify(policy)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-23T10:00:00", "2026-10-23T11:00:00"),
        ("2026-10-23T10:00:00+01:00", "2026-10-23T11:00:00+01:00"),
        ("2026-10-23T11:00:00Z", "2026-10-23T10:00:00Z"),
    ],
)
def test_window_invalid(start, end):
    policy = settings()
    policy["maintenance_windows_utc"] = [{"start": start, "end": end}]
    with pytest.raises(UpgradeError, match="UTC interval"):
        verify(policy)
