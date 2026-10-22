"""Device-scoped routing with durable pre-send acceptance and no replay queue."""

import asyncio

from .journal import Conflict, Journal
from .protocol import TERMINAL, Hello, Operation, Report


class ServiceError(ValueError):
    pass


class Peer:
    def __init__(self, websocket, hello: Hello, runtime_version=None):
        self.websocket = websocket
        self.hello = hello
        self.runtime_version = runtime_version
        self._send_lock = asyncio.Lock()

    async def send(self, message):
        async with self._send_lock:
            await asyncio.wait_for(self.websocket.send_json(message), 3)


class DeviceService:
    def __init__(self, journal: Journal, paired_devices):
        self.journal = journal
        self.paired_devices = frozenset(paired_devices)
        self.peers = {}
        self._lock = asyncio.Lock()
        self._submissions = set()
        self._waiters = {}
        self.closed = False

    async def attach(self, device_id, peer):
        async with self._lock:
            if self.closed or device_id in self.peers:
                raise ServiceError("device_already_connected_or_service_closed")
            self.peers[device_id] = peer

    async def detach(self, device_id, peer):
        async with self._lock:
            if self.peers.get(device_id) is peer:
                del self.peers[device_id]
                await self.journal.disconnect(device_id)
                self._notify_waiters()

    async def submit(self, operation: Operation, *, wait_timeout_ms: int = 0):
        if type(wait_timeout_ms) is not int or not 0 <= wait_timeout_ms <= 5000:
            raise ValueError("wait_timeout_ms must be an integer between 0 and 5000")
        # A cancelled HTTP caller must not leave an accepted journal write without
        # its bounded send/unknown finalization. Own that work independently.
        if self.closed:
            raise ServiceError("service_closed")
        task = asyncio.create_task(self._submit(operation))
        self._submissions.add(task)

        def done(t):
            self._submissions.discard(t)
            if not t.cancelled():
                t.exception()

        task.add_done_callback(done)
        record = await asyncio.shield(task)
        if wait_timeout_ms == 0 or record["state"] in TERMINAL | {"unknown"}:
            return record
        return await self._wait_for_result(operation.operation_id, wait_timeout_ms)

    def _notify_waiters(self, operation_id=None):
        groups = (
            self._waiters.values()
            if operation_id is None
            else (self._waiters.get(operation_id, ()),)
        )
        for events in groups:
            for event in events:
                event.set()

    async def _wait_for_result(self, operation_id, wait_timeout_ms):
        event = asyncio.Event()
        events = self._waiters.setdefault(operation_id, set())
        events.add(event)
        deadline = asyncio.get_running_loop().time() + wait_timeout_ms / 1000
        try:
            while True:
                # Register before reading; clear before each read so a concurrent
                # persisted report cannot be lost between the read and wait.
                event.clear()
                record = await self.journal.get(operation_id)
                if self.closed or record["state"] in TERMINAL | {"unknown"}:
                    return record
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return record
                try:
                    await asyncio.wait_for(event.wait(), remaining)
                except TimeoutError:
                    return await self.journal.get(operation_id)
        finally:
            events.discard(event)
            if not events:
                self._waiters.pop(operation_id, None)

    async def _submit(self, operation):
        async with self._lock:
            record = await self.journal.get(operation.operation_id)
            if record is not None:
                if record["digest"] != operation.digest:
                    raise Conflict("operation_id already belongs to another request")
                return record
            peer = self.peers.get(operation.device_id)
            if peer is None:
                raise ServiceError("device_offline")
            if operation.action not in peer.hello.capabilities:
                raise ServiceError("capability_unavailable")
            record, _ = await self.journal.accept(operation, "dispatching")
            try:
                await peer.send({"v": 1, "type": "execute", "operation": operation.model_dump()})
            except Exception:  # noqa: BLE001 - any transport send failure has an ambiguous outcome
                return await self.journal.update(
                    operation.operation_id, "unknown", expected={"dispatching", "sent", "running"}
                )
            return await self.journal.update(
                operation.operation_id, "sent", expected={"dispatching"}
            )

    async def report(self, device_id, peer, report: Report):
        async with self._lock:
            if self.peers.get(device_id) is not peer:
                raise ServiceError("stale_connection")
            record = await self.journal.get(report.operation_id)
            if (
                record is None
                or record["device_id"] != device_id
                or record["digest"] != report.digest
            ):
                raise ServiceError("unrecognized_operation")
            if record["state"] in TERMINAL and report.state not in TERMINAL:
                # A delayed running report cannot regress an established result.
                return
            result = report.result
            if result is not None:
                # Origin is derived from the authenticated peer and stored target,
                # never from a path or client-supplied server/local label.
                result = {
                    **result,
                    "location": {"namespace": "device", "device_id": device_id},
                    "action": record["operation"]["action"],
                }
            await self.journal.update(report.operation_id, report.state, result)
            self._notify_waiters(report.operation_id)

    def devices(self):
        return [
            {
                "device_id": device_id,
                "online": device_id in self.peers,
                "runtime_version": (
                    self.peers[device_id].runtime_version.model_dump()
                    if device_id in self.peers and self.peers[device_id].runtime_version is not None
                    else None
                ),
                "capabilities": self.peers[device_id].hello.capabilities
                if device_id in self.peers
                else [],
            }
            for device_id in sorted(self.paired_devices)
        ]

    async def close(self):
        self.closed = True
        self._notify_waiters()
        if self._submissions:
            await asyncio.gather(*tuple(self._submissions), return_exceptions=True)
        for device_id, peer in tuple(self.peers.items()):
            await peer.websocket.close(code=1001)
            await self.detach(device_id, peer)
