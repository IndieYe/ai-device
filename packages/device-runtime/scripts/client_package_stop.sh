set -eu
unit=ai-device-client.service
if [ -e /etc/systemd/system/$unit ] || [ -L /etc/systemd/system/$unit ]; then
    test ! -L /etc/systemd/system/$unit || exit 1
    grep -Fxq 'ExecStart=/opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_cli --config /etc/ai-device-client/client.json' /etc/systemd/system/$unit || {
        echo 'unrelated client service; refusing package operation' >&2; exit 1;
    }
fi
# Legacy layouts are never silently adopted or overwritten.
if [ -e /opt/ai-device/packages ] || [ -L /opt/ai-device/packages ] || [ -e /opt/ai-device/bootstrap ] || [ -L /opt/ai-device/bootstrap ]; then
    echo 'unsupported legacy Device installation; explicit migration required' >&2
    exit 1
fi
fragment=$(systemctl show "$unit" --property=FragmentPath --value) || exit 1
case "$fragment" in
    '') ;;
    /etc/systemd/system/ai-device-client.service) ;;
    *) echo 'external same-name service; refusing package operation' >&2; exit 1 ;;
esac
overrides=$(systemctl show "$unit" --property=DropInPaths --value) || exit 1
if [ -n "$overrides" ]; then
    echo 'client service has unsupported overrides; refusing package operation' >&2
    exit 1
fi
state=$(systemctl show "$unit" --property=ActiveState --value) || exit 1
main=$(systemctl show "$unit" --property=MainPID --value) || exit 1
control=$(systemctl show "$unit" --property=ControlPID --value) || exit 1
# A later unpack failure cannot roll back a stop, and dpkg does not call
# postinst afterwards. Say so while the operator is looking at this output.
case "$state:$main:$control" in
    active:*:0)
        echo 'note: stopping the client; if unpacking then fails it stays stopped — reinstall a complete package to repair' >&2
        ;;
esac
case "$state:$main:$control" in
    inactive:0:0|failed:0:0) ;;
    active:*:0)
        sigkill=$(systemctl show "$unit" --property=SendSIGKILL --value) || exit 1
        killmode=$(systemctl show "$unit" --property=KillMode --value) || exit 1
        if [ "$sigkill:$killmode" != no:control-group ]; then
            echo 'unsafe client shutdown policy; refusing package operation' >&2; exit 1
        fi
        /opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_package stop
        ;;
    *) echo 'client state unknown; refusing package replacement' >&2; exit 1 ;;
esac
# Check descendants too, without depending on an intact old Python runtime.
group=$(systemctl show "$unit" --property=ControlGroup --value) || exit 1
if [ -n "$group" ]; then
    case "$group" in /*) ;; *) exit 1 ;; esac
    case "$group" in *..*) exit 1 ;; esac
    # systemd reports this path relative to the cgroup root in both layouts,
    # but only the unified hierarchy mounts units there. The legacy hierarchy
    # keeps unit membership under the systemd controller mount and has no
    # cgroup.events file, so resolve the directory and read cgroup.procs.
    cgroup_root=${AI_DEVICE_CGROUP_ROOT:-/sys/fs/cgroup}
    if [ -f "$cgroup_root/cgroup.controllers" ]; then
        groupdir="$cgroup_root$group"
        if [ -d "$groupdir" ]; then
            grep -qx 'populated 0' "$groupdir/cgroup.events" || {
                echo 'client process group is not confirmed empty; refusing replacement' >&2; exit 1;
            }
        fi
    elif [ -d "$cgroup_root/systemd" ]; then
        groupdir="$cgroup_root/systemd$group"
        if [ -d "$groupdir" ]; then
            # cgroup.procs is a kernel pseudo-file: stat reports size 0 whether
            # it lists processes or not, so -s can never detect membership.
            # Read it. An unreadable file is not an empty one.
            procs=$(cat "$groupdir/cgroup.procs") || {
                echo 'client process group is unreadable; refusing replacement' >&2; exit 1;
            }
            if [ -n "$procs" ]; then
                echo 'client process group still has live processes; refusing replacement' >&2
                exit 1
            fi
        fi
    else
        echo 'warning: cannot determine the cgroup hierarchy; process group not verified' >&2
    fi
fi
