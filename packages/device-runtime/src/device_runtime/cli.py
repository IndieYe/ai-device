"""Separate server/client CLI entrypoints; secrets are read from environment only."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .bundle import BundleError, add_bundle_parser, run_bundle
from .installation import InstallationError, add_instance_parser, run_instance
from .protocol import MAX_MESSAGE_BYTES


def main():
    if sys.argv[1:2] == ["ops"]:
        from .operations_cli import main as operations_main

        raise SystemExit(operations_main(sys.argv[2:]))

    from .version import runtime_version

    parser = argparse.ArgumentParser(prog="device-runtime")
    parser.add_argument("--version", action="version", version=json.dumps(runtime_version()))
    modes = parser.add_subparsers(dest="mode", required=True)
    client = modes.add_parser("client")
    client.add_argument("--server-url", required=True)
    client.add_argument("--device-id", required=True)
    client.add_argument("--state-dir", type=Path, required=True)
    client.add_argument("--token-env", default="DEVICE_RUNTIME_TOKEN")
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
    server = modes.add_parser("server")
    server.add_argument("--state-dir", type=Path, required=True)
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--admin-token-env", default="DEVICE_RUNTIME_ADMIN_TOKEN")
    server.add_argument("--device-tokens-env", default="DEVICE_RUNTIME_DEVICE_TOKENS")
    modes.add_parser("ops", help="Device operations (device-runtime ops --help)")
    add_bundle_parser(modes)
    add_instance_parser(modes)
    args = parser.parse_args()
    try:
        if args.mode == "bundle":
            run_bundle(args)
        elif args.mode == "instance":
            run_instance(args)
        elif args.mode == "client":
            from .client import DeviceClient
            from .executor import LocalExecutor

            token = os.environ.get(args.token_env)
            if not token or len(token) < 16:
                raise ValueError("configure the device token environment variable")
            executor = LocalExecutor(
                args.state_dir, args.device_id, max_active_tasks=args.max_active_tasks
            )
            runtime = DeviceClient(
                server_url=args.server_url,
                device_id=args.device_id,
                token=token,
                state_dir=args.state_dir,
                executor=executor,
                max_concurrent_requests=args.max_concurrent_requests,
                max_concurrent_queries=args.max_concurrent_queries,
            )
            asyncio.run(runtime.run())
        else:
            if args.host not in ("127.0.0.1", "::1", "localhost"):
                raise ValueError(
                    "CLI server binds loopback only; use create_app behind a TLS proxy "
                    "for remote deployments"
                )
            import uvicorn

            from .server import create_app

            admin = os.environ.get(args.admin_token_env, "")
            devices = json.loads(os.environ.get(args.device_tokens_env, "{}"))
            if not isinstance(devices, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in devices.items()
            ):
                raise ValueError("configure a device_id to token mapping")
            app = create_app(args.state_dir, admin_token=admin, device_tokens=devices)
            uvicorn.run(
                app, host=args.host, port=args.port, ws_max_size=MAX_MESSAGE_BYTES, access_log=False
            )
    except InstallationError as exc:
        parser.exit(2, f"device-runtime instance: {exc}\n")
    except BundleError as exc:
        parser.exit(2, f"device-runtime bundle: {exc}\n")
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, ImportError) as exc:
        # Parsing errors must not echo a potentially secret JSON environment value.
        parser.exit(
            2,
            f"device-runtime: {type(exc).__name__}: configuration or runtime unavailable; "
            "see installation and state-directory requirements\n",
        )


if __name__ == "__main__":
    main()
