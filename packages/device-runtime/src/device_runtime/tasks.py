"""Device task identities and durable snapshots; never adopt a PID after restart.

The operation journal deduplicates each request. This separate private journal
tracks the task's changing lifetime; completed operation receipts remain immutable.
"""

import asyncio
import hashlib
import json
from pathlib import Path

from .journal import Journal
from .limits import DEFAULT_CONCURRENCY, positive_limit

OUTPUT_MAP_FILE = "task-output-map.json"
OUTPUT_MAP_FORMAT = 1
# Installed client environments declare their reader contract, independently of package version.
SUPPORTED_OUTPUT_MAP_FORMATS = (OUTPUT_MAP_FORMAT,)


def validate_output_map(paths):
    """Map only exact historical receipt paths to files under managed output/."""
    if not isinstance(paths, dict):
        raise TypeError("invalid task output map")
    for original, relative in paths.items():
        if not isinstance(original, str) or not original:
            raise ValueError("invalid task output receipt path")
        if relative is None:
            continue  # Explicitly nonportable, never fall back to its source.
        if (
            not Path(original).is_absolute()
            or ".." in Path(original).parts
            or not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or relative != Path(relative).as_posix()
            or relative == "."
        ):
            raise ValueError("invalid managed task output path")
    return paths


def output_map_metadata(root, paths):
    payload = {
        "format_version": OUTPUT_MAP_FORMAT,
        "instance_sha256": hashlib.sha256((root / "instance.json").read_bytes()).hexdigest(),
        "paths": validate_output_map(paths),
    }
    return {
        **payload,
        "sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
    }


def read_output_map(root):
    path = root / OUTPUT_MAP_FILE
    if path.is_symlink():
        raise ValueError("invalid task output map file")
    active_path = root / "active.json"
    active = json.loads(active_path.read_bytes()) if active_path.is_file() else {}
    if not isinstance(active, dict):
        raise TypeError("invalid task output map publication")
    if not path.exists():
        if "task_output_map_sha256" in active:
            raise ValueError("required task output map is missing")
        return None
    raw = path.read_bytes()
    if (
        active_path.is_file()
        and active.get("task_output_map_sha256") != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError("task output map does not match active publication")
    metadata = json.loads(raw)
    if not isinstance(metadata, dict) or "paths" not in metadata:
        raise ValueError("invalid task output map structure")
    expected = output_map_metadata(root, metadata["paths"])
    if metadata != expected:
        raise ValueError("task output map identity or checksum mismatch")
    return metadata["paths"]


class BackgroundTasks:
    def __init__(
        self,
        state_dir,
        device_id,
        runtime,
        run_command,
        offload,
        *,
        max_active_tasks=DEFAULT_CONCURRENCY,
    ):
        self.max_active_tasks = positive_limit(max_active_tasks)
        self.state_dir = Path(state_dir).resolve()
        self.journal = Journal(self.state_dir / "tasks.sqlite3", owner=f"tasks:{device_id}")
        self.runtime = runtime
        self.run_command = run_command
        self.offload = offload
        self._lock = asyncio.Lock()
        self._monitors = {}
        self._owned = set()
        self._live = set()
        self._closed = False

    @staticmethod
    def _metadata(result):
        return {key: value for key, value in result.items() if key != "background_pid"}

    @staticmethod
    def _state(result):
        if result.get("code") in {"BACKGROUND", "RUNNING"}:
            return "running"
        if result.get("exit_code") is None:
            return "unknown"
        return "succeeded" if result["exit_code"] == 0 else "failed"

    async def execute(self, operation):
        if self._closed:
            raise RuntimeError("device task manager is closed")
        await self.journal.initialize()
        if operation.action == "bash.execute":
            return await self._execute(operation)
        return await self._get(operation.arguments)

    async def _execute(self, operation):
        # Query/close and acceptance share a short boundary; command waits never hold it.
        async with self._lock:
            if self._closed:
                raise RuntimeError("device task manager is closed")
            _record, fresh = await self.journal.accept(operation, "running")
            if fresh:
                if len(self._owned | self._live) >= self.max_active_tasks:
                    result = {"success": False, "code": "TASK_CAPACITY_EXCEEDED", "exit_code": None}
                    await self.journal.update(operation.operation_id, "failed", result)
                    return result
                self._live.add(operation.operation_id)
        if not fresh:
            return await self._get(
                {"execution_id": operation.operation_id, "bytes": operation.arguments["bytes"]}
            )
        result = None
        try:
            result = await self.run_command(operation.arguments)
            state = self._state(result)
            if state == "unknown" and result.get("success") is False:
                state = "failed"
            result = {**result, "execution_id": operation.operation_id, "state": state}
            # PID is a runtime-private handle, never a public device identifier.
            pid = result.pop("background_pid", None)
            if state == "running":
                self._owned.add(operation.operation_id)
                ready = asyncio.Event()

                async def monitor():
                    await ready.wait()
                    await self._monitor(operation.operation_id, pid, result.get("output_path"))

                task = asyncio.create_task(monitor())
                self._monitors[operation.operation_id] = task
                task.add_done_callback(
                    lambda completed: self._monitor_done(operation.operation_id, completed)
                )
                try:
                    await self.journal.update(operation.operation_id, state, result)
                finally:
                    ready.set()
            else:
                await self.journal.update(operation.operation_id, state, result)
            return result
        except BaseException:
            await self.journal.update(
                operation.operation_id, "unknown", result, expected={"running"}
            )
            raise
        finally:
            self._live.discard(operation.operation_id)

    def _monitor_done(self, execution_id, task):
        if self._monitors.get(execution_id) is task:
            self._monitors.pop(execution_id, None)
        if not task.cancelled():
            task.exception()

    async def _get(self, arguments):
        async with self._lock:
            if self._closed:
                raise RuntimeError("device task manager is closed")
            return await self._read(arguments)

    async def _read(self, arguments):
        record = await self.journal.get(arguments["execution_id"])
        if record is None:
            return {
                "success": False,
                "code": "EXECUTION_NOT_FOUND",
                "state": "unknown",
                "execution_id": arguments["execution_id"],
            }
        if (
            record["state"] == "running"
            and record["operation_id"] not in self._monitors
            and record["operation_id"] not in self._live
        ):
            record = await self.journal.update(
                record["operation_id"], "unknown", record["result"], expected={"running"}
            )
        result = dict(record["result"] or {})
        state = record["state"]
        result.update(
            execution_id=record["operation_id"],
            state=state,
            success=state in {"running", "succeeded"},
        )
        if state == "unknown":
            result.update(code="UNKNOWN", exit_code=None)
        elif state == "running":
            result["code"] = "RUNNING"
        path = result.get("output_path")
        if path:

            def tail():
                target = self._output_path(path)
                if target is None:
                    raise FileNotFoundError("output unavailable")
                with target.open("rb") as stream:
                    stream.seek(0, 2)
                    size = stream.tell()
                    stream.seek(max(0, size - arguments["bytes"]))
                    data = stream.read(arguments["bytes"])
                return size, data

            try:
                size, data = await self.offload(tail)
                result.update(
                    output=data.decode("utf-8", errors="replace"),
                    total_bytes=size,
                    returned_output_bytes=len(data),
                    truncated=size > len(data),
                )
            except (OSError, ValueError, TypeError):
                result.update(output="", output_unavailable=True)
        return result

    async def _monitor(self, task_id, pid, output_path):
        try:
            while True:
                result = await self.runtime.get_background_result(pid, timeout=0, output_bytes=0)
                state = self._state(result)
                if state != "running":
                    metadata = self._metadata(result)
                    if metadata["output_path"] is None:
                        previous = await self.journal.get(task_id)
                        metadata["output_path"] = (previous["result"] or {}).get(
                            "output_path"
                        ) or output_path
                    await self.journal.update(task_id, state, metadata, expected={"running"})
                    if result.get("exit_code") is not None:
                        self._owned.discard(task_id)
                    return
                await asyncio.sleep(0.1)
        except BaseException:
            record = await self.journal.get(task_id)
            await self.journal.update(
                task_id,
                "unknown",
                record["result"] or {"output_path": output_path},
                expected={"running"},
            )
            raise

    def _output_path(self, path):
        if not isinstance(path, str):
            return None
        output = self.state_dir / "output"
        # Standalone state directories retain their direct-run path semantics.
        # A restored instance carries a receipt-specific map, never a basename
        # guess or a preference for a still-existing original instance.
        mapping = None
        if self.state_dir.name == "client" and self.state_dir.parent.name == "state":
            mapping = read_output_map(self.state_dir.parent.parent)
        if mapping is None:
            return Path(path)
        if path in mapping:
            if mapping[path] is None:
                return None
            target = output / mapping[path]
        else:
            target = Path(path)
            if not target.is_absolute() or not target.is_relative_to(output):
                return None
        if not target.resolve().is_relative_to(output):
            return None
        return target

    async def aclose(self):
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = list(self._monitors.values())
            for task in tasks:
                task.cancel()
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
                # Only this runtime's owned executions/drains are cancelled and
                # reaped. The task-journal lease remains held until they are gone.
                await self.runtime.aclose()
                self._owned.clear()
            finally:
                await self.journal.close()
