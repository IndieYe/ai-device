"""Linux disposable fixture for real guard/sender descriptor inheritance."""
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

from device_runtime import native_helper as helper


def main():
    if sys.platform != "linux" or os.getpid() != 1 or os.geteuid() != 0:
        raise RuntimeError("disposable Linux container PID 1 and root required")
    # All files and sockets remain inside this fresh temporary container.
    with tempfile.TemporaryDirectory(prefix="device-guard-fd-", dir="/run") as temp:
        root = Path(temp)
        registry = root / "registry"
        registry.mkdir(mode=0o700)
        sender = root / "sender"
        report = root / "report.json"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(root / "helper.sock"))
            listener.listen(1)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(root / "helper.sock"))
                connection, _ = listener.accept()
                with connection:
                    os.set_inheritable(listener.fileno(), True)
                    os.set_inheritable(connection.fileno(), True)
                    wanted = [(os.fstat(s.fileno()).st_dev, os.fstat(s.fileno()).st_ino)
                              for s in (listener, connection)]
                    sender.write_text(
                        f"#!{sys.executable}\n"
                        "import json,os\n"
                        f"wanted={wanted!r}\n"
                        "def present(pid):\n"
                        "    found=[]\n"
                        "    for name in os.listdir(f'/proc/{pid}/fd'):\n"
                        "        try: s=os.stat(f'/proc/{pid}/fd/{name}')\n"
                        "        except FileNotFoundError: continue\n"
                        "        if (s.st_dev,s.st_ino) in wanted: found.append(name)\n"
                        "    return found\n"
                        f"with open({str(report)!r},'w') as out:\n"
                        "    json.dump({'guard':present(os.getppid()),'sender':present(os.getpid())},out)\n"
                    )
                    sender.chmod(0o700)
                    with helper.package_lease(registry) as package_fd, (root / "execution.lock").open("a+b") as lease:
                        helper.Systemd(str(sender)).start(
                            "fixture", lease_fd=lease.fileno(), package_fd=package_fd,
                            receipt_path=root / "receipt.json", intent={"fixture": True},
                        )
        assert json.loads(report.read_text()) == {"guard": [], "sender": []}
        assert json.loads((root / "receipt.json").read_text())["state"] == "sender_reaped"
    print("GUARD_SENDER_HELPER_SOCKET_NOT_INHERITED_FIXTURE_OK", flush=True)


if __name__ == "__main__":
    main()
