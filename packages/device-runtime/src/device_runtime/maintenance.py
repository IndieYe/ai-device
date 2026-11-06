"""Local Unix socket maintenance gate; no arbitrary commands or service management."""

import asyncio
import fcntl
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path


class MaintenanceServer:
    def __init__(self, client, path):
        self.client = client
        self.path = Path(path)
        self.server = None
        self.identity = None
        self.lease = None
        self.connections = set()

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
        owner = None
        self.connections.add(writer)
        try:
            data = json.loads(await asyncio.wait_for(reader.readline(), 5))
            if not isinstance(data, dict):
                raise TypeError("invalid maintenance request")
            if data.get("action") == "acquire":
                # Server-generated ownership prevents a second connection from
                # resuming someone else's pause using a caller-selected ID.
                owner = "lease_" + uuid.uuid4().hex
                result = await self.client.maintenance_acquire(owner)
                writer.write(json.dumps(result).encode() + b"\n")
                await writer.drain()
                if result["acquired"]:
                    # EOF or any release byte ends the lease, including an
                    # installer crash. The installer holds this socket until
                    # the service is confirmed stopped, not just asked to stop.
                    # Legacy callers close or send any release byte. Only
                    # this same acquired connection may request instance exit.
                    command = await reader.read(1)
                    if command == b"P":
                        # One bounded round-trip proves this lease's peer is
                        # still responsive after the caller sampled identity.
                        challenge = await asyncio.wait_for(reader.readexactly(32), 5)
                        if self.client._maintenance_exit_callback is None:
                            raise ValueError("lease peer binding is not enabled")
                        writer.write(challenge)
                        await writer.drain()
                        command = await reader.read(1)
                    if command == b"X":
                        result = await self.client.maintenance_exit(owner)
                        writer.write(json.dumps(result).encode() + b"\n")
                        await writer.drain()
                return
            upgrade_id = data.get("upgrade_id")
            if not isinstance(upgrade_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", upgrade_id
            ):
                raise ValueError("invalid maintenance ID")
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
        except (
            ValueError,
            OSError,
            TimeoutError,
            KeyError,
            TypeError,
            asyncio.IncompleteReadError,
        ):
            writer.write(b'{"error":"maintenance_unavailable"}\n')
        finally:
            if owner is not None:
                await self.client.maintenance_release(owner)
            self.connections.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def close(self):
        try:
            if self.server is not None:
                self.server.close()
                # Python 3.13 waits for existing clients too. Close lease sockets
                # before waiting: installers intentionally hold them until exit.
                for writer in tuple(self.connections):
                    writer.close()
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


class ClientBusy(RuntimeError):
    """The client has work in flight; no admission state was changed."""


@contextmanager
def idle_lease(path, *, timeout=5):
    """Hold admission until the caller confirms service stop or abandons it.

    This is not a persisted upgrade transaction. Closing the socket releases
    admission if the client is still alive; a stopped client needs no release.
    The caller must use the OS service manager and verify its terminal state
    inside this context before replacing any program files.
    """
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(b'{"action":"acquire"}\n')
        data = b""
        while not data.endswith(b"\n"):
            chunk = connection.recv(4096)
            if not chunk or len(data) + len(chunk) > 4096:
                raise ValueError("invalid maintenance response")
            data += chunk
        result = json.loads(data)
        if not isinstance(result, dict) or type(result.get("acquired")) is not bool:
            raise ValueError("client maintenance unavailable")
        if not result["acquired"]:
            raise ClientBusy("client is busy; retry when tasks finish")
        yield IdleLease(connection)


class IdleLease:
    """Single-use exit request on the already acquired socket, never reconnect.

    Response loss is unknown, not safe to retry against a successor instance.
    A successful response is commitment only, not proof that cleanup completed.
    """

    def __init__(self, connection):
        self._connection = connection
        self._exit_attempted = False
        self._binding_attempted = False

    def bind_peer(self, sample):
        """Sample identity around a fresh same-connection round trip, once.

        sample(socket) must use kernel peer identity, not service-label lookup.
        Failure poisons exit on this lease; never retry on another connection.
        """
        import secrets

        if self._binding_attempted or self._exit_attempted:
            raise ValueError("lease peer binding already attempted")
        self._binding_attempted = True
        try:
            before = sample(self._connection)
            if before is None:
                raise ValueError("lease peer unavailable")
            nonce = secrets.token_bytes(32)
            self._connection.sendall(b"P" + nonce)
            response = b""
            while len(response) < 32:
                part = self._connection.recv(32 - len(response))
                if not part:
                    raise ValueError("lease peer unavailable")
                response += part
            if response != nonce or sample(self._connection) != before:
                raise ValueError("lease peer changed")
            return before
        except (OSError, ValueError):
            self._exit_attempted = True
            raise ValueError("lease peer binding unknown; exit refused") from None

    def request_exit(self):
        if self._exit_attempted:
            raise ValueError("lease exit already attempted; do not replay")
        self._exit_attempted = True
        try:
            self._connection.sendall(b"X")
            data = b""
            while not data.endswith(b"\n"):
                chunk = self._connection.recv(4096)
                if not chunk or len(data) + len(chunk) > 4096:
                    raise ValueError("lease exit result unknown; do not replay")
                data += chunk
            result = json.loads(data)
            if not isinstance(result, dict) or result.get("exit_committed") is not True:
                raise ValueError("lease exit refused or unknown; do not replay")
        except (OSError, ValueError):
            raise ValueError("lease exit refused or unknown; do not replay") from None
        return {"exit_committed": True}
