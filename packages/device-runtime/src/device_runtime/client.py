"""Reconnect transport does not retry executions; reports have a durable outbox."""

import asyncio
import ipaddress
import json
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

from pydantic import TypeAdapter
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .journal import Conflict, Journal
from .limits import DEFAULT_CONCURRENCY, positive_limit
from .protocol import MAX_MESSAGE_BYTES, Ack, Execute, Identifier, Operation, Report, canonical
from .version import VERSION_HEADER, version_header


def device_url(base_url: str, device_id: str):
    TypeAdapter(Identifier).validate_python(device_id)
    url = urlsplit(base_url)
    if url.scheme not in ("ws", "wss") or not url.hostname or url.username or url.password:
        raise ValueError("use ws/wss URL without embedded credentials")
    if (
        url.query
        or url.fragment
        or url.path not in ("", "/", "/device-runtime", "/device-runtime/")
    ):
        raise ValueError("use server root or /device-runtime mount without query or fragment")
    try:
        loopback = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = url.hostname == "localhost"
    if url.scheme != "wss" and not loopback:
        raise ValueError("non-loopback devices require wss")
    return base_url.rstrip("/") + "/v1/devices/" + quote(device_id, safe="") + "/connect"


class DeviceClient:
    def __init__(
        self,
        *,
        server_url: str,
        device_id: str,
        token: str,
        state_dir: Path,
        executor,
        reconnect_delay=1,
        max_concurrent_requests=DEFAULT_CONCURRENCY,
        max_concurrent_queries=DEFAULT_CONCURRENCY,
    ):
        self.max_concurrent_requests = positive_limit(max_concurrent_requests)
        self.max_concurrent_queries = positive_limit(max_concurrent_queries)
        self.url = device_url(server_url, device_id)
        if len(token) < 16 or not token.isascii() or not token.isprintable():
            raise ValueError("configure a printable ASCII device token of at least 16 characters")
        self.device_id = device_id
        self.token = token
        self.journal = Journal(Path(state_dir) / "operations.sqlite3", owner=self.url)
        self.executor = executor
        self.reconnect_delay = reconnect_delay
        self._socket = None
        self._send_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._tasks = set()
        self._closed = asyncio.Event()
        self._close_task = None
        self._initialized = False
        self._failure = None

    async def _send(self, message):
        async with self._send_lock:
            socket = self._socket
            if socket is None:
                return
            try:
                await asyncio.wait_for(socket.send(canonical(message)), 3)
            except (ConnectionClosed, OSError, TimeoutError):
                # Results remain unconfirmed in the journal and replay on reconnect.
                return

    async def _report(self, record):
        report = Report(
            v=1,
            type="report",
            operation_id=record["operation_id"],
            digest=record["digest"],
            state=record["state"],
            result=record["result"],
        )
        await self._send(report.model_dump())

    async def _reconcile(self):
        after = ""
        while records := await self.journal.unconfirmed(after):
            for record in records:
                await self._report(record)
            after = records[-1]["operation_id"]

    async def _accept(self, operation: Operation):
        if operation.device_id != self.device_id:
            raise Conflict("operation targets another device")
        report_record = None
        # Acceptance, execution registration and cleanup share one lifetime. Close
        # may set the stop flag while a transaction is pending, but cannot release
        # its lease or miss a subsequently registered execution.
        async with self._lifecycle_lock:
            if self._closed.is_set():
                return
            record, fresh = await self.journal.accept(operation, "running")
            if self._closed.is_set():
                if fresh:
                    await self.journal.update(
                        operation.operation_id, "unknown", expected={"running"}
                    )
                return
            if not fresh:
                report_record = record
            elif sum(
                getattr(task, "_device_query", False) == (operation.action == "bash.get")
                for task in self._tasks
            ) >= (
                self.max_concurrent_queries
                if operation.action == "bash.get"
                else self.max_concurrent_requests
            ):
                result = {
                    "location": {"namespace": "device", "device_id": self.device_id},
                    "data": {"success": False, "error": "client_capacity_exceeded"},
                }
                report_record = await self.journal.update(operation.operation_id, "failed", result)
            else:
                task = asyncio.create_task(self._execute(operation))
                task._device_query = operation.action == "bash.get"
                self._track_execution(task)
        if report_record is not None and not self._closed.is_set():
            await self._report(report_record)

    def _track_execution(self, task):
        self._tasks.add(task)

        def done(t):
            self._tasks.discard(t)
            # A persistence failure is fatal to this connection, not a safe retry.
            if not t.cancelled() and t.exception() is not None:
                self._failure = t.exception()
                self._closed.set()
                if self._socket is not None:
                    asyncio.create_task(self._socket.close(code=1011))

        task.add_done_callback(done)

    async def _execute(self, operation):
        await self._report(await self.journal.get(operation.operation_id))
        try:
            state, result = await self.executor(operation)
        except asyncio.CancelledError:
            await self.journal.update(operation.operation_id, "unknown", expected={"running"})
            raise
        except Exception as exc:  # noqa: BLE001 - execution boundary; no journal/network work here
            state, result = (
                "failed",
                {
                    "location": {"namespace": "device", "device_id": self.device_id},
                    "data": {"success": False, "error": type(exc).__name__},
                },
            )
        if len(canonical(result).encode()) > MAX_MESSAGE_BYTES - 2048:
            result = {
                "location": {"namespace": "device", "device_id": self.device_id},
                "data": {
                    "success": state == "succeeded",
                    "result_omitted": True,
                    "reason": "result_too_large",
                },
            }
        record = await self.journal.update(operation.operation_id, state, result)
        await self._report(record)

    async def run(self):
        try:
            async with self._lifecycle_lock:
                if not self._closed.is_set():
                    await self.journal.initialize()
                    self._initialized = True
            while not self._closed.is_set():
                try:
                    async with connect(
                        self.url,
                        additional_headers={
                            "Authorization": "Bearer " + self.token,
                            VERSION_HEADER: version_header(),
                        },
                        max_size=MAX_MESSAGE_BYTES,
                        open_timeout=10,
                        proxy=None,
                    ) as socket:
                        self._socket = socket
                        await self._send(
                            {
                                "v": 1,
                                "type": "hello",
                                "capabilities": list(self.executor.capabilities),
                                "platform": sys.platform,
                            }
                        )
                        welcome = json.loads(await asyncio.wait_for(socket.recv(), 10))
                        if welcome != {"v": 1, "type": "welcome", "device_id": self.device_id}:
                            raise ValueError("invalid welcome")
                        # Read acknowledgements concurrently with replay, otherwise
                        # a large outbox can deadlock both directions on backpressure.
                        replay = asyncio.create_task(self._reconcile())

                        def replay_done(t):
                            if not t.cancelled() and t.exception() is not None:
                                self._failure = t.exception()
                                self._closed.set()
                                asyncio.create_task(socket.close(code=1011))

                        replay.add_done_callback(replay_done)
                        try:
                            async for raw in socket:
                                message = json.loads(raw)
                                if message.get("type") == "execute":
                                    await self._accept(Execute.model_validate(message).operation)
                                elif message.get("type") == "ack":
                                    ack = Ack.model_validate(message)
                                    await self.journal.confirm(ack.operation_id, ack.state)
                                else:
                                    raise ValueError("unexpected server message")
                        finally:
                            replay.cancel()
                            await asyncio.gather(replay, return_exceptions=True)
                except (ConnectionClosed, OSError, TimeoutError):
                    pass
                except InvalidStatus as exc:
                    if exc.response.status_code not in (429, 500, 502, 503, 504):
                        raise RuntimeError(
                            "device pairing or connection endpoint rejected"
                        ) from None
                finally:
                    self._socket = None
                if not self._closed.is_set():
                    try:
                        await asyncio.wait_for(self._closed.wait(), self.reconnect_delay)
                    except TimeoutError:
                        pass
        finally:
            await self.close()
        if self._failure is not None:
            raise self._failure

    async def close(self):
        self._closed.set()
        if self._close_task is None:

            async def cleanup():
                try:
                    if self._socket is not None:
                        await self._socket.close(code=1001)
                finally:
                    async with self._lifecycle_lock:
                        for task in tuple(self._tasks):
                            task.cancel()
                        try:
                            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
                            await self.executor.aclose()
                        finally:
                            if self._initialized:
                                try:
                                    await self.journal.disconnect(self.device_id)
                                finally:
                                    await self.journal.close()

            self._close_task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            while not self._close_task.done():
                try:
                    await asyncio.shield(self._close_task)
                except asyncio.CancelledError:
                    continue
            self._close_task.result()
            raise
