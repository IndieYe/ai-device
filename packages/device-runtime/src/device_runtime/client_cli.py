"""Client-only CLI, independent of bundle and legacy upgrade management."""

import argparse
import asyncio
import json
import os
from pathlib import Path


def add_arguments(client):
    client.add_argument("--server-url", required=True)
    client.add_argument("--device-id", required=True)
    client.add_argument("--state-dir", type=Path, required=True)
    client.add_argument("--token-env", default="DEVICE_RUNTIME_TOKEN")
    client.add_argument(
        "--maintenance", action="store_true", help="Enable local upgrade control socket"
    )
    from .limits import DEFAULT_CONCURRENCY, positive_limit

    def concurrency(value):
        try:
            return positive_limit(int(value))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "concurrency limits must be positive integers"
            ) from exc

    for name in ("max-concurrent-requests", "max-concurrent-queries", "max-active-tasks"):
        client.add_argument(
            f"--{name}",
            type=concurrency,
            default=DEFAULT_CONCURRENCY,
            help="positive concurrency limit (default: %(default)s)",
        )


def run(args, *, token=None, lease_exit=False):
    from .client import DeviceClient
    from .executor import LocalExecutor

    if token is None:
        token = os.environ.get(args.token_env)
    if not token or len(token) < 16:
        raise ValueError("configure the device token environment variable")
    executor = LocalExecutor(args.state_dir, args.device_id, max_active_tasks=args.max_active_tasks)
    if lease_exit:
        import sys

        if sys.platform != "darwin":
            raise ValueError("lease exit requires the managed macOS entry")
    runtime = DeviceClient(
        server_url=args.server_url,
        device_id=args.device_id,
        token=token,
        state_dir=args.state_dir,
        executor=executor,
        maintenance=args.maintenance,
        max_concurrent_requests=args.max_concurrent_requests,
        max_concurrent_queries=args.max_concurrent_queries,
    )

    async def run_client():
        import signal

        loop = asyncio.get_running_loop()
        task = asyncio.create_task(runtime.run())
        if lease_exit:
            runtime._maintenance_exit_callback = lambda: loop.call_soon(task.cancel)
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            runtime._maintenance_exit_callback = None
            loop.remove_signal_handler(signal.SIGTERM)

    asyncio.run(run_client())


def main(argv=None, *, lease_exit=False):
    from .version import runtime_version

    parser = argparse.ArgumentParser(prog="device-runtime client")
    parser.add_argument("--version", action="version", version=json.dumps(runtime_version()))
    # Installed services use a single explicit config file; direct CLI flags
    # remain available for development and existing center-side launchers.
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    configured = any(item == "--config" or item.startswith("--config=") for item in arguments)
    if configured:
        parser.add_argument("--config", required=True, type=Path)
    else:
        add_arguments(parser)
    args = parser.parse_args(arguments)
    # The package manager enforces the declared floor, but the installed
    # service is started from an absolute path without consulting it. Refuse a
    # too-old host here so the failure names the real cause instead of surfacing
    # as a dynamic-loader error inside the first message.
    from .client_host_glibc import UnsupportedHostError, check_host

    check_host()
    try:
        if configured:
            from .client_config import load

            settings, token = load(args.config)
            if lease_exit:
                run(settings, token=token, lease_exit=True)
            else:
                run(settings, token=token)
        else:
            if lease_exit:
                raise ValueError("lease exit requires installed configuration")
            run(args)
    except KeyboardInterrupt:
        pass
    except UnsupportedHostError as exc:
        # The message states the required and detected glibc; it carries no
        # configuration, so show it rather than the generic unavailable text.
        parser.exit(2, f"device-runtime client: {exc}\n")
    except (ValueError, RuntimeError, ImportError, OSError) as exc:
        # The cause is included because this is the only thing a failing service
        # start leaves behind: launchd has no console, so the device log is the
        # whole record. These exceptions come from configuration validation and
        # local I/O, whose messages name a field or a path and never carry the
        # token itself -- the token is checked for shape, not echoed.
        parser.exit(
            2,
            f"device-runtime client: {type(exc).__name__}: configuration or runtime "
            f"unavailable: {exc}\n",
        )


if __name__ == "__main__":
    main()
