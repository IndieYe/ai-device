"""One-time configuration as the intended service user; no service side effects."""

import argparse
import getpass
import sys
from pathlib import Path

from .client_config import initialize


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Complete missing configuration files without overwriting existing files",
    )
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument(
        "--token-stdin", action="store_true", help="Read token from stdin, never argv"
    )
    args = parser.parse_args(argv)
    try:
        if args.token_stdin:
            token = sys.stdin.read(4098).rstrip("\r\n")
        else:
            if not sys.stdin.isatty():
                parser.error("use --token-stdin for non-interactive configuration")
            token = getpass.getpass("Device token: ")
        path = initialize(
            args.config_dir,
            server_url=args.server_url,
            device_id=args.device_id,
            state_dir=args.state_dir,
            token=token,
            resume=args.resume,
        )
    except FileExistsError:
        parser.exit(
            2,
            "configuration directory already exists; inspect its state, then use --resume only for matching inputs; existing files were not overwritten\n",
        )
    except (ValueError, TypeError, OSError):
        parser.exit(2, "configuration failed; check URL, identity, token, paths and permissions\n")
    print(f"Configured: {path}. Service registration/start is still required.")


if __name__ == "__main__":
    main()
