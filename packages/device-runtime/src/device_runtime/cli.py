"""Separate server/client CLI entrypoints; secrets are read from environment only."""

import argparse
import json
import os
import sys
from pathlib import Path

from .protocol import MAX_MESSAGE_BYTES


def main():
    if sys.argv[1:2] == ["client"]:
        from .client_cli import main as client_main

        return client_main(sys.argv[2:])

    from .bundle import BundleError, add_bundle_parser, run_bundle
    from .installation import InstallationError, add_instance_parser, run_instance

    if sys.argv[1:2] == ["ops"]:
        from .operations_cli import main as operations_main

        raise SystemExit(operations_main(sys.argv[2:]))

    from .version import runtime_version

    parser = argparse.ArgumentParser(prog="device-runtime")
    parser.add_argument("--version", action="version", version=json.dumps(runtime_version()))
    modes = parser.add_subparsers(dest="mode", required=True)
    from .client_cli import add_arguments

    add_arguments(modes.add_parser("client"))
    server = modes.add_parser("server")
    server.add_argument("--state-dir", type=Path, required=True)
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--admin-token-env", default="DEVICE_RUNTIME_ADMIN_TOKEN")
    server.add_argument("--device-tokens-env", default="DEVICE_RUNTIME_DEVICE_TOKENS")
    modes.add_parser("ops", help="Device operations (device-runtime ops --help)")
    add_bundle_parser(modes)
    from . import upgrader

    upgrader.add_parser(modes)
    add_instance_parser(modes)
    args = parser.parse_args()
    try:
        if args.mode == "upgrade":
            upgrader.run(args)
            return
        if args.mode == "bundle":
            run_bundle(args)
        elif args.mode == "instance":
            run_instance(args)
        elif args.mode == "client":
            from .client_cli import run

            run(args)
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
    except (ValueError, RuntimeError, ImportError, OSError) as exc:
        # Parsing errors must not echo a potentially secret JSON environment value.
        parser.exit(
            2,
            f"device-runtime: {type(exc).__name__}: configuration or runtime unavailable; "
            "see installation and state-directory requirements\n",
        )


if __name__ == "__main__":
    main()
