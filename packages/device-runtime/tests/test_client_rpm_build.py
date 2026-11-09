import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/build-client-rpm.py"
spec = importlib.util.spec_from_file_location("client_rpm_build", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

DEB_SCRIPT = Path(__file__).parents[1] / "scripts/build-client-deb.py"


def _spec_text(version="0.4.3", tree="/TREE"):
    return module.SPEC.format(
        version=version,
        minimum_glibc=module.MINIMUM_GLIBC,
        tree=tree,
        buildarch="x86_64",
        PRE_GUARD=module.PRE_GUARD,
        stop=module.STOP,
        preun_stop=module.PREUN_STOP,
    )


def test_declared_glibc_floor_matches_the_runtime_requirement():
    """The RPM builder keeps its own literal so it can run without importing
    the package; something has to check that it still agrees."""
    from device_runtime import dependencies
    from device_runtime.client_host_glibc import MINIMUM_GLIBC as CLIENT_FLOOR

    assert module.MINIMUM_GLIBC == dependencies.MINIMUM_GLIBC == CLIENT_FLOOR
    assert module.MINIMUM_GLIBC == dependencies.TARGET["glibc"]


def test_stop_guard_is_shared_with_the_deb_builder():
    """Two copies of a security check drift apart. The builders must read the
    same file, not hold parallel strings."""
    deb = importlib.util.spec_from_file_location("client_deb_build", DEB_SCRIPT)
    deb_module = importlib.util.module_from_spec(deb)
    deb.loader.exec_module(deb_module)

    assert module.STOP == deb_module.STOP
    assert module.STOP.strip()


def test_spec_requires_the_declared_floor_not_the_build_host():
    text = _spec_text()
    assert f"glibc >= {module.MINIMUM_GLIBC}" in text
    assert "glibc >= 2.39" not in text
    assert text.count("glibc >= ") == 1


def test_spec_stops_the_client_before_unpacking():
    """%pre runs before unpack, so a refusal there leaves the old package
    intact. %preun runs after the new payload is in place; stopping there would
    kill the version %post just started."""
    text = _spec_text()
    pre = text.split("%pre\n", 1)[1].split("%post\n", 1)[0]
    preun = text.split("%preun\n", 1)[1].split("%postun\n", 1)[0]

    assert "client_package stop" in pre
    preun = preun.replace("%preun\n", "", 1)
    # No stop at all outside the erase branch.
    assert preun.count("client_package stop") <= 1


def test_preun_stops_the_service_on_full_erase():
    """Measured on alinux3: with an empty erase branch the client kept running
    after rpm -e deleted its unit file, leaving an orphan that systemd showed as
    a not-found unit which was still active. %postun cannot help -- it runs
    after the payload is gone."""
    text = _spec_text()
    preun = text.split("%preun\n", 1)[1].split("%postun\n", 1)[0]
    assert 'if [ "${1:-0}" -eq 0 ]; then' in preun
    # The full shared guard, not a bare "systemctl stop": a bare stop skips the
    # busy refusal, the unsafe-shutdown-policy refusal and the process-group
    # check, and reports success even when the client is still running.
    assert "client_package stop --from-package-script" in preun
    assert "refusing package operation" in preun
    assert "refusing package replacement" in preun


def test_preun_does_not_stop_during_upgrade():
    """An upgrade leaves one instance behind ($1 = 1). %pre already stopped the
    old client before unpacking, so %preun must not stop the new version that
    %post just started."""
    text = _spec_text()
    preun = text.split("%preun\n", 1)[1].split("%postun\n", 1)[0]
    assert "-eq 0" in preun
    # The stop is inside the erase branch only.
    branch = preun.split('if [ "${1:-0}" -eq 0 ]; then', 1)[1]
    assert "client_package stop" in branch
    # %pre already stopped the old client; a stop here would kill the new one
    # that %post just started and told the operator was locally ready.
    outside = preun.split('if [ "${1:-0}" -eq 0 ]; then', 1)[0]
    assert "client_package stop" not in outside


def test_spec_has_no_dpkg_only_verbs():
    """rpm has no purge; erase must keep configuration and state."""
    text = _spec_text()
    assert "purge" not in text.replace("# RPM has no purge verb", "")
    postun = text.split("%postun\n", 1)[1]
    # The config directory may be named for the ExecStart comparison, but no
    # scriptlet may delete it. That is what a DEB purge does and an RPM erase
    # must not.
    assert "rm -rf /etc/ai-device-client" not in postun
    assert "rm -rf /etc/ai-device-client" not in text


def test_spec_defines_all_four_scriptlets():
    text = _spec_text()
    for section in ("%pre\n", "%post\n", "%preun\n", "%postun\n"):
        assert section in text


def test_pre_skips_the_guard_on_a_first_install():
    """%pre runs before unpack, so the stop guard would execute the python3 of
    the *installed* package. On a first install there is none and that call
    exits 127, aborting an install that has nothing to stop.

    Measured: the guard failed with "No such file or directory" and rpm -i then
    failed outright. %1 is 1 on a first install and 2 when upgrading in.
    """
    text = _spec_text()
    pre = text.split("%pre\n", 1)[1].split("%post\n", 1)[0]
    guard = pre.split("\n\n", 1)[0]
    assert 'if [ "${1:-0}" -eq 1 ]; then' in guard
    assert "exit 0" in guard
    # The guard must come before anything that touches the installed tree.
    assert pre.index("${1:-0}") < pre.index("client_package stop")


def test_pre_stops_the_client_when_upgrading():
    text = _spec_text()
    pre = text.split("%pre\n", 1)[1].split("%post\n", 1)[0]
    assert "client_package stop" in pre


def test_spec_does_not_let_rpm_rewrite_the_prebuilt_payload():
    """The tree ships a prebuilt python and its libraries. rpm's post-install
    steps rewrote them: measured on alinux3, python3.13 differed in 2,051,694
    bytes after packaging and then aborted with "undefined symbol", while the
    same file ran fine before. Shebang mangling also repointed python3 scripts
    at the host's /usr/libexec/platform-python, an interpreter the package does
    not own.
    """
    text = _spec_text()
    assert "%global __os_install_post %{nil}" in text
    assert "%global __strip /bin/true" in text
    assert "%global __brp_mangle_shebangs %{nil}" in text
    assert "%global __brp_check_shebang %{nil}" in text


def test_spec_does_not_turn_bundled_libraries_into_host_requires():
    """Dependency discovery found the payload's own libpng/libavif/libtiff and
    emitted 67 soname Requires, including libtcl9.0.so which is not a system
    library. No host could satisfy those. Only the four real host dependencies
    may remain."""
    text = _spec_text()
    assert "AutoReqProv: no" in text
    assert "%global __requires_exclude .*" in text
    # The real dependencies are still declared explicitly.
    assert "Requires: systemd" in text
    assert f"Requires: glibc >= {module.MINIMUM_GLIBC}" in text
    assert "Requires: ca-certificates" in text
    assert "Requires: util-linux" in text


def test_install_normalises_payload_permissions():
    """Measured on alinux3: client_host_glibc.py arrived in the tree as 0600.
    The service runs as an unprivileged user and imports that module right after
    parsing, so the service exited 1 with PermissionError, and rpm's %post then
    failed the readiness check. Mode bits from a hand-assembled tree cannot be
    trusted into the payload."""
    text = _spec_text()
    install = text.split("%install\n", 1)[1].split("%files\n", 1)[0]
    assert "-type d -exec chmod 0755" in install
    # Executables are selected with -perm /111, not -perm -u+x: the blanket
    # 0644 below clears the execute bits, so any test that runs after it
    # matches nothing. Measured on alinux3 -- doing it in the wrong order
    # turned python3.13 into 0644 and every following scriptlet died with
    # "Permission denied" (exit 126).
    assert "-perm /111 -exec chmod 0755" in install
    assert "! -perm /111 -exec chmod 0644" in install
    assert install.index("-perm /111 -exec chmod 0755") < install.index(
        "! -perm /111 -exec chmod 0644"
    )


def test_generated_scriptlets_are_shell_valid(tmp_path):
    """Each scriptlet body must survive sh -n. This runs the real interpreter;
    a monkeypatched subprocess.run would prove nothing."""
    text = _spec_text()
    for name in ("pre", "post", "preun", "postun"):
        body = text.split(f"%{name}\n", 1)[1]
        for later in ("%pre\n", "%post\n", "%preun\n", "%postun\n", "%files"):
            if later in body:
                body = body.split(later, 1)[0]
        script = tmp_path / f"{name}.sh"
        script.write_text(body)
        result = subprocess.run(
            ["sh", "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"%{name}: {result.stderr}"


def test_rejects_versions_that_would_break_the_spec():
    for bad in ("", "v0.4.3", "0.4.3-1", "0.4.3 beta", "0.4.3;rm -rf /"):
        with pytest.raises(ValueError):
            module._rpm_version(bad)


def test_accepts_a_normal_version():
    assert module._rpm_version("0.4.3") == "0.4.3"
