"""Standalone service app. No AI host bootstrap or production configuration reads."""

import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from .journal import Conflict, Journal
from .protocol import MAX_MESSAGE_BYTES, Hello, Identifier, Operation, Report
from .service import DeviceService, Peer, ServiceError
from .version import VERSION_HEADER, parse_version_header, runtime_version


def authenticated(header, token):
    return bool(header and hmac.compare_digest(header, "Bearer " + token))


def create_app(state_dir: Path, *, admin_token: str, device_tokens: dict[str, str]):
    if not device_tokens or min(len(admin_token), *(len(t) for t in device_tokens.values())) < 16:
        raise ValueError(
            "configure separate nonempty admin and device tokens of at least 16 characters"
        )
    if admin_token in device_tokens.values() or len(set(device_tokens.values())) != len(
        device_tokens
    ):
        raise ValueError("admin and per-device tokens must be distinct")
    for token in (admin_token, *device_tokens.values()):
        if not token.isascii() or not token.isprintable():
            raise ValueError("tokens must be printable ASCII")
    for device_id in device_tokens:
        TypeAdapter(Identifier).validate_python(device_id)
    journal = Journal(Path(state_dir) / "operations.sqlite3")
    service = DeviceService(journal, device_tokens)

    @asynccontextmanager
    async def lifespan(app):
        await journal.initialize()
        try:
            yield
        finally:
            try:
                await service.close()
            finally:
                await journal.close()

    app = FastAPI(lifespan=lifespan)
    app.state.device_service = service

    async def admin(authorization: str | None = Header(default=None)):
        if not authenticated(authorization, admin_token):
            raise HTTPException(401, "unauthorized")

    @app.get("/v1/version", dependencies=[Depends(admin)])
    def version():
        return {"role": "center", **runtime_version()}

    @app.get("/v1/devices", dependencies=[Depends(admin)])
    async def devices():
        return service.devices()

    @app.post("/v1/operations", dependencies=[Depends(admin)])
    async def submit(operation: Operation, wait_timeout_ms: int = Query(default=0, ge=0, le=5000)):
        try:
            return await service.submit(operation, wait_timeout_ms=wait_timeout_ms)
        except (ServiceError, Conflict) as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/v1/operations/{operation_id}", dependencies=[Depends(admin)])
    async def operation(operation_id: str):
        record = await journal.get(operation_id)
        if record is None:
            raise HTTPException(404, "operation_not_found")
        return record

    @app.websocket("/v1/devices/{device_id}/connect")
    async def connect(websocket: WebSocket, device_id: str):
        token = device_tokens.get(device_id)
        if token is None or not authenticated(websocket.headers.get("authorization"), token):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        peer = None
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), 10)
            if len(raw.encode()) > MAX_MESSAGE_BYTES:
                raise ServiceError("message_too_large")
            hello = Hello.model_validate_json(raw)
            peer = Peer(
                websocket, hello, parse_version_header(websocket.headers.get(VERSION_HEADER))
            )
            await service.attach(device_id, peer)
            await peer.send({"v": 1, "type": "welcome", "device_id": device_id})
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode()) > MAX_MESSAGE_BYTES:
                    raise ServiceError("message_too_large")
                report = Report.model_validate_json(raw)
                await service.report(device_id, peer, report)
                await peer.send(
                    {
                        "v": 1,
                        "type": "ack",
                        "operation_id": report.operation_id,
                        "state": report.state,
                    }
                )
        except (WebSocketDisconnect, OSError):
            pass
        except (ValidationError, ServiceError, Conflict, TimeoutError):
            await websocket.close(code=1008)
        finally:
            if peer is not None:
                await service.detach(device_id, peer)

    return app
