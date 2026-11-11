"""Offline checks that one release carries a single, fully declared version set.

These guards exist because the version is declared in four coupled places and
`bundle.build_bundle` only validates the *format* of ``release_version``: it
never compares it with the versions recorded inside the component wheels. A
release that bumps one place and forgets another therefore still passes
delivery verification while recording a stale version in the bundle manifest.

Nothing here builds, uploads or contacts a registry.
"""

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]

BASH = ROOT / "packages/bash/pyproject.toml"
FS = ROOT / "packages/fs/pyproject.toml"
DEVICE = ROOT / "packages/device-runtime/pyproject.toml"
DEVICE_INIT = ROOT / "packages/device-runtime/src/device_runtime/__init__.py"

# The two client-side distributions pinned by the device package, and the
# pyproject that owns each of them.
PINNED = {
    "ai-device-bash": BASH,
    "ai-device-fs": FS,
}


def _project(path):
    with path.open("rb") as handle:
        return tomllib.load(handle)["project"]


def device_version():
    """Read `ai-device`'s version from its hatch source of truth.

    The device package declares ``dynamic = ["version"]``; the authoritative
    value lives in ``device_runtime.__version__``.
    """
    source = DEVICE_INIT.read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', source, re.MULTILINE)
    assert match, "device_runtime.__version__ must stay a plain module-level literal"
    return match.group(1)


def test_declared_versions_are_pep440():
    for path in (BASH, FS):
        Version(_project(path)["version"])
    Version(device_version())
    assert _project(DEVICE)["dynamic"] == ["version"], (
        "ai-device must keep hatch dynamic versioning as the single source"
    )


def test_device_version_matches_hatch_source():
    """The hatch reader and this test must agree on where the version lives."""
    hatch = tomllib.loads(DEVICE.read_text(encoding="utf-8"))["tool"]["hatch"]["version"]
    assert hatch["path"] == "src/device_runtime/__init__.py"


def test_client_pins_match_sibling_versions():
    """`ai-device[client]` must pin exactly the sibling versions we release.

    A stale pin would install a previously published pair alongside a new
    device release, so this is the check that keeps the three packages moving
    together.
    """
    extras = _project(DEVICE)["optional-dependencies"]
    requirements = {
        canonicalize_name(requirement.name): requirement
        for requirement in map(Requirement, extras["client"])
    }
    assert set(requirements) == set(PINNED), (
        "the client extra must pin exactly ai-device-bash and ai-device-fs"
    )
    for name, path in PINNED.items():
        requirement = requirements[name]
        assert requirement.url is None and requirement.marker is None
        (specifier,) = requirement.specifier
        assert specifier.operator in ("==", "==="), (
            f"{name} must be pinned with ==, not {specifier.operator}"
        )
        assert no_wildcard(specifier.version), (
            f"{name} must pin one exact version, not {specifier.version}"
        )
        assert specifier.version.lstrip("=") == _project(path)["version"], (
            f"{name} pins {specifier.version} but {path.name} declares "
            f"{_project(path)['version']}"
        )


def no_wildcard(version):
    return "*" not in version


def test_declared_state_format_is_audited():
    """Persistent-state compatibility is audited per format, not per version.

    Bumping ``__version__`` says nothing about whether the bytes on disk still
    parse, so auditing version numbers made this check meaningless in both
    directions: it demanded a fresh human review for releases that changed
    nothing persistent, and it went stale the moment nobody remembered to add
    an entry. The thing that actually breaks an upgrade is a change of data
    format, so that is what requires an entry here.

    This test does not register anything; it fails when a new format was
    introduced without being confirmed.
    """
    from device_runtime.bundle import STATE_FORMAT
    from device_runtime.lifecycle import AUDITED_STATE_FORMATS

    assert STATE_FORMAT in AUDITED_STATE_FORMATS, (
        f"state format {STATE_FORMAT} has no AUDITED_STATE_FORMATS entry; "
        "validate its persistent data contract and add it explicitly"
    )


def test_state_format_is_not_hardcoded_outside_its_definition():
    """One definition, so a format bump cannot half-apply.

    ``state_format`` is written into release manifests, instance bindings and
    backup files, and compared when reading them back. Literal copies of the
    number compile fine and would let a bump update some sites and not others,
    producing artifacts that disagree about the format they record.
    """
    package = ROOT / "packages/device-runtime/src/device_runtime"
    owners = {"bundle.py"}
    offenders = []
    pattern = re.compile(r'"state_format":\s*\d+|\["state_format"\]\s*!=\s*\d+')
    for path in sorted(package.glob("*.py")):
        if path.name in owners:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, (
        "state_format must reference STATE_FORMAT from bundle.py, not a literal: "
        f"{offenders}"
    )


def test_release_scripts_do_not_hardcode_the_device_version():
    """Guards the fix that removes duplicate version literals from tooling.

    Kept as a separate assertion so a future edit cannot reintroduce a second
    source of truth without failing a named test.
    """
    offenders = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'release_version\s*=\s*"([^"]+)"', text):
            offenders.append(f"{path.name}: release_version={match.group(1)}")
    assert not offenders, (
        "release scripts must derive the version from built artifacts, not "
        f"hardcode it: {offenders}"
    )
