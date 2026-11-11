import pytest

from device_runtime import client_host_glibc


@pytest.mark.parametrize(
    ("reported", "accepted"),
    [
        ("2.17", True),
        ("2.28", True),
        ("2.32", True),
        ("2.39", True),
        ("2.4", False),
        ("2.16", False),
        ("1.99", False),
    ],
)
def test_floor_is_enforced_at_the_boundary(monkeypatch, reported, accepted):
    """2.17 passes; anything below it is refused, including the closest miss."""
    monkeypatch.setattr(
        client_host_glibc, "host_glibc", lambda: client_host_glibc._version_tuple(reported)
    )
    if accepted:
        client_host_glibc.check_host()
    else:
        with pytest.raises(client_host_glibc.UnsupportedHostError):
            client_host_glibc.check_host()


def test_minor_versions_compare_numerically(monkeypatch):
    """2.9 must not pass as newer than 2.17 through string comparison."""
    monkeypatch.setattr(
        client_host_glibc, "host_glibc", lambda: client_host_glibc._version_tuple("2.9")
    )
    with pytest.raises(client_host_glibc.UnsupportedHostError):
        client_host_glibc.check_host()


def test_unknown_host_version_does_not_block_startup(monkeypatch):
    """An unprobeable host is not evidence of an incompatible one.

    musl and unusual builds have no glibc answer to compare; the dynamic loader
    remains the authority and reports precisely if the runtime cannot load.
    """
    monkeypatch.setattr(client_host_glibc, "host_glibc", lambda: None)
    client_host_glibc.check_host()


def test_error_names_required_and_detected_versions(monkeypatch):
    monkeypatch.setattr(
        client_host_glibc, "host_glibc", lambda: client_host_glibc._version_tuple("2.12")
    )
    with pytest.raises(client_host_glibc.UnsupportedHostError) as excinfo:
        client_host_glibc.check_host()
    message = str(excinfo.value)
    assert "2.17" in message
    assert "2.12" in message
