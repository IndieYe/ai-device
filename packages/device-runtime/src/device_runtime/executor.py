"""Use paired stage-one local cores only; never resolve a server-host path."""

import asyncio
import base64
import sys
from pathlib import Path

from .limits import DEFAULT_CONCURRENCY, positive_limit
from .protocol import CAPABILITIES, ImagePayload, Operation
from .tasks import BackgroundTasks


class LocalExecutor:
    capabilities = CAPABILITIES

    def __init__(self, state_dir: Path, device_id: str, *, max_active_tasks=DEFAULT_CONCURRENCY):
        max_active_tasks = positive_limit(max_active_tasks)
        if sys.platform not in ("darwin", "linux"):
            raise RuntimeError("only macOS/Linux clients are supported")
        try:
            from mcp_server_bash.artifacts import cleanup_artifacts
            from mcp_server_bash.core import (
                ExecutionRequest,
                ExecutionRuntime,
                ExecutionTarget,
                create_script_artifacts,
                create_stdin_artifact,
            )
            from mcp_server_fs.core import (
                ImageReadError,
                edit_file,
                insert_file,
                read_file,
                read_image,
                write_file,
            )
        except ImportError:
            raise RuntimeError(
                "install the paired Bash/FS core builds; published 0.1.0 alone "
                "does not prove that the core API exists"
            ) from None
        self._create_script = create_script_artifacts
        self._create_stdin = create_stdin_artifact
        self._cleanup_artifacts = cleanup_artifacts
        self._image_error = ImageReadError
        self._read_image = read_image
        self._request = ExecutionRequest
        self._target = ExecutionTarget
        self.runtime = ExecutionRuntime(
            output_dir=str(state_dir / "output"), artifact_dir=str(state_dir / "artifacts")
        )
        self.files = {
            "fs.read_file": read_file,
            "fs.write_file": write_file,
            "fs.edit_file": edit_file,
            "fs.insert_file": insert_file,
        }
        self.device_id = device_id
        self._file_results = state_dir / "file-results"
        self._workers = set()
        self._closed = False
        self._close_task = None
        self.tasks = BackgroundTasks(
            state_dir,
            device_id,
            self.runtime,
            self._run_command,
            self._offload,
            max_active_tasks=max_active_tasks,
        )

    def _track_worker(self, coroutine):
        worker = asyncio.create_task(coroutine)
        self._workers.add(worker)

        def done(task):
            self._workers.discard(task)
            if not task.cancelled():
                task.exception()

        worker.add_done_callback(done)
        return worker

    async def _offload(self, function, *args, **kwargs):
        return await asyncio.shield(
            self._track_worker(asyncio.to_thread(function, *args, **kwargs))
        )

    async def _prepare_stdin(self, text):
        worker = self._track_worker(
            asyncio.to_thread(self._create_stdin, text.encode("utf-8"), self.runtime.artifact_dir)
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # A preparation thread may finish after its caller is cancelled. Keep
            # ownership and clean that artifact before executor shutdown returns.
            async def discard():
                _, artifacts = await worker
                await asyncio.to_thread(
                    self._cleanup_artifacts, artifacts, self.runtime.artifact_dir
                )

            self._track_worker(discard())
            raise

    def _image_data(self, arguments):
        image = self._read_image(**arguments)
        info = image.info
        return {
            "success": True,
            "path": image.path,
            "image": ImagePayload(
                mime_type=info.mime_type,
                base64=base64.b64encode(info.data).decode("ascii"),
                size=info.size,
                width=info.width,
                height=info.height,
                animated=info.animated,
            ).model_dump(),
        }

    async def _run_command(self, args):
        stdin_path, artifacts = None, None
        handed_off = False
        script_path = None
        mode = args["mode"]
        if mode == "script":
            worker = self._track_worker(
                asyncio.to_thread(
                    self._create_script,
                    args["script"].encode(),
                    args["stdin"].encode() if args.get("stdin") is not None else None,
                    self.runtime.artifact_dir,
                )
            )
            try:
                script_path, stdin_path, artifacts = await asyncio.shield(worker)
            except asyncio.CancelledError:

                async def discard():
                    _, _, pending = await worker
                    await asyncio.to_thread(
                        self._cleanup_artifacts, pending, self.runtime.artifact_dir
                    )

                self._track_worker(discard())
                raise
        elif args.get("stdin") is not None:
            stdin_path, artifacts = await self._prepare_stdin(args["stdin"])
        try:
            request = self._request(
                tool_mode=mode,
                command=args.get("command"),
                argv=tuple(args["argv"]) if mode == "run" else None,
                script_path=script_path,
                stdin_path=stdin_path,
                env_overrides={},
                target=self._target(cwd=args["cwd"]),
                artifacts=artifacts,
            )
            data = await self.runtime.run_execution(
                request,
                timeout=args["timeout"],
                output_bytes=args["bytes"],
                timeout_behavior=args["timeout_behavior"],
                max_output_file_bytes=args["output_limit_bytes"],
            )
            handed_off = data.get("code") == "BACKGROUND"
            return data
        finally:
            # Background drains, not the short start request, own stdin lifetime.
            if artifacts is not None and not handed_off:
                await self._offload(self._cleanup_artifacts, artifacts, self.runtime.artifact_dir)

    async def __call__(self, operation: Operation):
        if self._closed:
            raise RuntimeError("local executor is closed")
        args = operation.arguments
        if operation.action in {"bash.execute", "bash.get"}:
            data = await self.tasks.execute(operation)
        elif operation.action == "fs.read_image":
            try:
                data = await self._offload(self._image_data, args)
            except self._image_error as exc:
                data = {"success": False, "code": exc.code, "message": exc.message}
        else:
            # Cancellation cannot stop a filesystem thread. Keep ownership until
            # the thread finishes, even if its awaiting operation becomes unknown.
            options = (
                {"temp_dir": self._file_results}
                if operation.action in {"fs.edit_file", "fs.insert_file"}
                else {}
            )
            result = await self._offload(self.files[operation.action], **args, **options)
            data = result.data
        # read_file's successful payload has content, not a success key. Its
        # failure contract is an error field; writes/edits and Bash use success.
        success = "error" not in data if operation.action == "fs.read_file" else data["success"]
        state = "succeeded" if success else "failed"
        return state, {
            "location": {"namespace": "device", "device_id": self.device_id},
            "action": operation.action,
            "data": data,
        }

    async def aclose(self):
        self._closed = True
        if self._close_task is None:

            async def cleanup():
                await self.tasks.aclose()
                while self._workers:
                    await asyncio.gather(*tuple(self._workers), return_exceptions=True)
                await self.runtime.aclose()

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
