#!/bin/sh
# Only a freshly created disposable systemd Linux VM/container; never a host.
# Package must already be installed. No center or real credentials are used.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = yes ] || exit 2
[ "$(id -u)" = 0 ] || exit 2
[ "$(cat /proc/1/comm)" = systemd ] || exit 2
[ ! -e /opt/ai-device/instances ] || exit 2
unset SYSTEMD_LOG_LEVEL
useradd -m -u 12345 nativeprobe
mkdir -p /var/lib/ai-device /opt/ai-device/instances
chown nativeprobe:nativeprobe /opt/ai-device/instances
runuser -u nativeprobe -- ai-device-native-install --root /opt/ai-device/instances/probe \
 --instance-id package-probe --server-url http://127.0.0.1:9
printf 'DEVICE_RUNTIME_TOKEN=fixture-not-real\n' > /opt/ai-device/fixture.env
chmod 600 /opt/ai-device/fixture.env
ai-device-native-helper deploy \
 --bootstrap-python /opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13 \
 --registry /var/lib/ai-device/manager --unit-dir /etc/systemd/system \
 --runtime-dir /run/ai-device-helper --group 12345
ai-device-native-helper register --registry /var/lib/ai-device/manager \
 --unit-dir /etc/systemd/system --root /opt/ai-device/instances/probe \
 --instance-id package-probe --username nativeprobe --environment-file /opt/ai-device/fixture.env
systemd-analyze verify /etc/systemd/system/ai-device-native-helper.service \
 /etc/systemd/system/ai-device-client-package-probe.service
systemctl daemon-reload
systemctl start ai-device-native-helper
for attempt in 1 2 3 4 5; do
 [ ! -S /run/ai-device-helper/helper.sock ] || break
 sleep 1
done
systemctl is-active ai-device-native-helper
call_owner() {
 runuser -u nativeprobe -- ai-device-native-helper call \
  --socket /run/ai-device-helper/helper.sock --action "$1" \
  --instance-id package-probe --operation-id "$2"
}
call_owner query before-start
call_owner start start-probe-1
sleep 2
systemctl is-active ai-device-client-package-probe
pid=$(systemctl show ai-device-client-package-probe -p MainPID --value)
[ "$pid" -gt 1 ]
[ "$(awk '/^Uid:/ {print $2}' /proc/"$pid"/status)" = 12345 ]
call_owner start start-probe-1
call_owner reconcile start-probe-1
[ "$(systemctl show ai-device-client-package-probe -p MainPID --value)" = "$pid" ]
useradd -m -u 12346 otherprobe
usermod -a -G nativeprobe otherprobe
if runuser -u otherprobe -- ai-device-native-helper call \
 --socket /run/ai-device-helper/helper.sock --action start \
 --instance-id package-probe --operation-id forbidden-start; then
 echo 'ERROR: nonowner was accepted' >&2
 exit 1
fi
[ "$(systemctl show ai-device-client-package-probe -p MainPID --value)" = "$pid" ]
systemctl enable ai-device-native-helper ai-device-client-package-probe
systemctl is-enabled ai-device-native-helper ai-device-client-package-probe
printf 'verified isolated systemd service, UID12345, stable PID, nonowner refusal, enable links\n'
printf 'NOT verified: center health, pairing, host reboot, crash recovery, update/uninstall\n'
