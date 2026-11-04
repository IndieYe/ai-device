"""Run only inside the disposable DEB acceptance container.

A loopback fake center dispatches a real finite task to the installed client.
The package install must refuse without stopping it. No real devices involved.
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from websockets.asyncio.server import serve


async def main(background=False, package="/tmp/client3.deb"):
    connected = asyncio.Event()
    finished = asyncio.Event()
    marker = Path("/var/lib/ai-device-client/busy-probe-started")
    marker.unlink(missing_ok=True)
    done_marker = marker.with_name("busy-probe-finished")
    done_marker.unlink(missing_ok=True)
    operation_id = "busy_" + uuid.uuid4().hex

    async def handle(socket):
        await socket.recv()
        await socket.send(json.dumps({"v": 1, "type": "welcome", "device_id": "deb-test"}))
        await socket.send(
            json.dumps(
                {
                    "v": 1,
                    "type": "execute",
                    "operation": {
                        "operation_id": operation_id,
                        "device_id": "deb-test",
                        "action": "bash.execute",
                        "arguments": {
                            "argv": ["/bin/sh", "-c", f"touch {marker}; sleep 12; touch {done_marker}"],
                            "cwd": "/",
                            "timeout": 0.2 if background else 30,
                            "bytes": 100,
                        },
                    },
                }
            )
        )
        connected.set()
        async for raw in socket:
            message = json.loads(raw)
            if message.get("operation_id") == operation_id:
                await socket.send(
                    json.dumps(
                        {
                            "v": 1,
                            "type": "ack",
                            "operation_id": operation_id,
                            "state": message["state"],
                        }
                    )
                )
                if message["state"] in ("succeeded", "failed", "unknown"):
                    assert message["state"] == "succeeded", message
                    finished.set()

    async with serve(handle, "127.0.0.1", 18765):
        async with asyncio.timeout(40):
            await connected.wait()
            while not marker.exists():
                await asyncio.sleep(0.05)
            if background:
                await finished.wait()  # execution request returned, child still running
                assert not done_marker.exists()
            before = await asyncio.create_subprocess_exec(
                "systemctl",
                "show",
                "ai-device-client.service",
                "-p",
                "MainPID",
                "--value",
                stdout=asyncio.subprocess.PIPE,
            )
            pid, _ = await before.communicate()
            install = await asyncio.create_subprocess_exec(
                "dpkg",
                "-i",
                package,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await install.communicate()
            assert install.returncode != 0, output.decode()
            assert b"busy with active tasks" in output, output.decode()
            assert b"retry when tasks finish" in output, output.decode()
            after = await asyncio.create_subprocess_exec(
                "systemctl",
                "show",
                "ai-device-client.service",
                "-p",
                "MainPID",
                "--value",
                stdout=asyncio.subprocess.PIPE,
            )
            pid_after, _ = await after.communicate()
            assert pid == pid_after and pid.strip() != b"0", (pid, pid_after)
            await finished.wait()
            while not done_marker.exists():
                await asyncio.sleep(0.05)
            print(f"PASS: background={background}; package refused; same PID; task completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--package", default="/tmp/client3.deb")
    args = parser.parse_args()
    asyncio.run(main(args.background, args.package))
