set -eu
unit=ai-device-client.service
# No registered service means nothing to stop.
if [ ! -e /etc/systemd/system/$unit ]; then
    exit 0
fi
# systemd 219 has no --value; --all includes empty properties.
read_property() {
    key=$1
    property=$(systemctl show "$unit" --all --property="$key") || return 1
    # Spell the percent as an escape: RPM embeds this shell verbatim.
    case "$property" in
        *'
'*) return 1 ;;
        "$key="*) format=$(printf '\045s'); printf "$format\n" "${property#*=}" ;;
        *) return 1 ;;
    esac
}
state=$(read_property ActiveState) || exit 1
main=$(read_property MainPID) || exit 1
control=$(read_property ControlPID) || exit 1
case "$main" in ''|*[!0-9]*) exit 1 ;; esac
case "$control" in ''|*[!0-9]*) exit 1 ;; esac
# A later unpack failure cannot roll back a stop, and dpkg does not call
# postinst afterwards. Say so while the operator is looking at this output.
case "$state:$main:$control" in
    inactive:0:0|failed:0:0) ;;
    active:*:0)
        echo 'note: stopping the client; if unpacking then fails it stays stopped — reinstall a complete package to repair' >&2
        /opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_package stop --from-package-script
        ;;
    *) echo 'client state unknown; refusing package replacement' >&2; exit 1 ;;
esac
