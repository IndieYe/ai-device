"""Local Unix socket maintenance gate; no arbitrary commands or service management."""

import asyncio
import fcntl
import json
import os
import stat
from pathlib import Path


class MaintenanceServer:
    def __init__(self, client, path):
        self.client = client
        self.path = Path(path)
        self.server = None
        self.identity = None
        self.lease = None

    async def start(self):
        try:
            await self._start()
        except BaseException:
            await self.close()
            raise

    async def _start(self):
        parent = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or parent.st_mode & 0o077
        ):
            raise ValueError("maintenance directory must be private and owned by client")
        lock_path = self.path.with_suffix(".lock")
        if lock_path.is_symlink():
            raise ValueError("invalid maintenance lease")
        self.lease = lock_path.open("a+b")
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("maintenance socket already exists; owner active") from None
        if os.path.lexists(self.path):
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                raise ValueError("maintenance socket already exists; unknown owner")
            try:
                _, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(self.path)), 1)
            except ConnectionRefusedError:
                # Private directory plus exclusive lifetime lease plus refused
                # connection proves a dead cooperating owner, not just an old PID.
                self.path.unlink()
            else:
                writer.close()
                await writer.wait_closed()
                raise ValueError("maintenance socket already exists; owner active")
        self.server = await asyncio.start_unix_server(self.handle, path=str(self.path), limit=4096)
        info = self.path.lstat()
        self.identity = (info.st_dev, info.st_ino)
        try:
            self.path.chmod(0o600)
        except OSError:
            await self.close()
            raise

    async def handle(self, reader, writer):
        try:
            data = json.loads(await asyncio.wait_for(reader.readline(), 5))
            if not isinstance(data, dict):
                raise TypeError("invalid maintenance request")
            upgrade_id = data.get("upgrade_id")
            from .upgrader import _operation_id

            _operation_id(upgrade_id)
            if data.get("action") == "drain":
                result = await self.client.maintenance_drain(upgrade_id)
            elif data.get("action") == "status":
                result = await self.client.maintenance_status(upgrade_id)
            elif data.get("action") == "resume":
                result = await self.client.maintenance_resume(upgrade_id)
            else:
                raise ValueError("unsupported maintenance action")
            writer.write(json.dumps(result).encode() + b"\n")
            await writer.drain()
        except (ValueError, OSError, TimeoutError, KeyError, TypeError):
            writer.write(b'{"error":"maintenance_unavailable"}\n')
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def close(self):
        try:
            if self.server is not None:
                self.server.close()
                await self.server.wait_closed()
                self.server = None
                try:
                    info = self.path.lstat()
                except FileNotFoundError:
                    return
                if (info.st_dev, info.st_ino) == self.identity:
                    self.path.unlink()
        finally:
            if self.lease is not None:
                self.lease.close()
                self.lease = None


def request(path, action, upgrade_id, *, timeout=5):
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(
            json.dumps({"action": action, "upgrade_id": upgrade_id}).encode() + b"\n"
        )
        data = b""
        while not data.endswith(b"\n"):
            chunk = connection.recv(4096)
            if not chunk or len(data) + len(chunk) > 4096:
                raise ValueError("invalid maintenance response")
            data += chunk
        result = json.loads(data)
        if not isinstance(result, dict) or result.get("upgrade_id") != upgrade_id:
            raise ValueError("maintenance operation mismatch")
        return result
