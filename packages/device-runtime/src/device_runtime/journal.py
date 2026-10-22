"""Durable device-operation journal, not an AI Session database.

Every public operation runs as a complete synchronous unit via asyncio.to_thread.
Terminal results are immutable. This is at-most-once acceptance, not exactly-once effects.
"""

import asyncio
import fcntl
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .protocol import TERMINAL, Operation, canonical


class Conflict(ValueError):
    pass


class Journal:
    def __init__(self, path: Path, *, owner="server"):
        self.path = Path(path)
        self.owner = owner
        self._lease = None
        self._initialize_lock = asyncio.Lock()

    async def _work(self, function):
        # Complete a transaction before propagating caller cancellation. A thread
        # cannot be cancelled; dropping its await would race recovery/lease release.
        task = asyncio.create_task(asyncio.to_thread(function))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            if not task.cancelled():
                task.exception()
            raise

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    async def initialize(self):
        async with self._initialize_lock:
            if self._lease is not None:
                return
            await self._initialize()

    async def _initialize(self):
        def work():
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lease = self.path.with_suffix(".lock").open("a+")
            try:
                self.path.with_suffix(".lock").chmod(0o600)
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                lease.close()
                raise
            self._lease = lease
            with self._connect() as db:
                # WAL/SHM inherit the main DB mode; protect before creating them.
                self.path.chmod(0o600)
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE IF NOT EXISTS metadata (owner TEXT NOT NULL)")
                identity = db.execute("SELECT owner FROM metadata").fetchone()
                if identity is None:
                    db.execute("INSERT INTO metadata(owner) VALUES (?)", (self.owner,))
                elif identity["owner"] != self.owner:
                    raise Conflict("state directory belongs to another device/server URL")
                db.execute("""CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                    digest TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                    result TEXT, confirmed INTEGER NOT NULL DEFAULT 0)""")
                self.path.chmod(0o600)
                db.execute(
                    "UPDATE operations SET state='unknown', confirmed=0 "
                    "WHERE state NOT IN ('succeeded','failed','unknown')"
                )

        try:
            await self._work(work)
        except BaseException:
            await self.close()
            raise

    async def close(self):
        def work():
            if self._lease is not None:
                self._lease.close()
                self._lease = None

        await self._work(work)

    @staticmethod
    def _record(row):
        if row is None:
            return None
        result = dict(row)
        result["operation"] = json.loads(result.pop("payload"))
        result["result"] = json.loads(result["result"]) if result["result"] is not None else None
        return result

    async def get(self, operation_id):
        def work():
            with self._connect() as db:
                return self._record(
                    db.execute(
                        "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
                    ).fetchone()
                )

        return await self._work(work)

    async def accept(self, operation: Operation, state):
        def work():
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM operations WHERE operation_id=?", (operation.operation_id,)
                ).fetchone()
                if row is not None:
                    if row["digest"] != operation.digest:
                        raise Conflict("operation_id already belongs to another request")
                    return self._record(row), False
                db.execute(
                    "INSERT INTO operations(operation_id,device_id,digest,payload,state) "
                    "VALUES (?,?,?,?,?)",
                    (
                        operation.operation_id,
                        operation.device_id,
                        operation.digest,
                        canonical(operation.model_dump()),
                        state,
                    ),
                )
                return self._record(
                    db.execute(
                        "SELECT * FROM operations WHERE operation_id=?", (operation.operation_id,)
                    ).fetchone()
                ), True

        return await self._work(work)

    async def update(self, operation_id, state, result=None, *, expected=None):
        def work():
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(operation_id)
                if expected is not None and row["state"] not in expected:
                    return self._record(row)
                if row["state"] in TERMINAL:
                    if row["state"] != state or row["result"] != canonical(result):
                        raise Conflict("terminal result cannot change")
                    return self._record(row)
                db.execute(
                    "UPDATE operations SET state=?,result=?,confirmed=0 WHERE operation_id=?",
                    (state, canonical(result) if result is not None else None, operation_id),
                )
                return self._record(
                    db.execute(
                        "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
                    ).fetchone()
                )

        return await self._work(work)

    async def disconnect(self, device_id):
        def work():
            with self._connect() as db:
                db.execute(
                    "UPDATE operations SET state='unknown',confirmed=0 WHERE device_id=? "
                    "AND state IN ('dispatching','sent','running')",
                    (device_id,),
                )

        await self._work(work)

    async def unconfirmed(self, after=""):
        def work():
            with self._connect() as db:
                return [
                    self._record(r)
                    for r in db.execute(
                        "SELECT * FROM operations WHERE confirmed=0 AND operation_id>? "
                        "ORDER BY operation_id LIMIT 100",
                        (after,),
                    ).fetchall()
                ]

        return await self._work(work)

    async def confirm(self, operation_id, state):
        def work():
            with self._connect() as db:
                db.execute(
                    "UPDATE operations SET confirmed=1 WHERE operation_id=? "
                    "AND state=? AND state IN ('succeeded','failed','unknown')",
                    (operation_id, state),
                )

        await self._work(work)
