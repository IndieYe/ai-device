"""Configuration loading and logger setup for mcp-server-bash."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .env_keys import SERVICE_CONFIG_ENV_KEYS

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Explicit whitelist of settings the process environment may override. Keeping the
# list closed prevents unrelated variables from silently becoming configuration.
ENV_OVERRIDE_KEYS = SERVICE_CONFIG_ENV_KEYS


def load_env_config(project_root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Load configuration from the project root .env and the process environment.

    The process environment wins over the file so that a host can configure an
    installed wheel, where ``parents[2]`` no longer reaches the source checkout.
    Values are read one key at a time and never written back into ``os.environ``:
    the execution backend inherits that mapping for every spawned command, so
    importing dotenv-style loading would leak ``BASH_CALLER_CONTEXT_TOKEN`` into
    user shells.
    """
    config: dict[str, str] = {}
    env_file = project_root / ".env"

    if env_file.exists():
        with env_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    config[key.strip()] = value.strip()

    for key in ENV_OVERRIDE_KEYS:
        value = os.environ.get(key)
        if value is not None:
            config[key] = value

    return config


def parse_bool_env(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_int_env(value: Optional[str], default: int, min_value: int = 0) -> int:
    if value is None:
        return default
    try:
        return max(min_value, int(value))
    except ValueError:
        return default


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    env_config: dict[str, str]
    transport: str
    sse_port: int
    sse_host: str
    default_cwd: Optional[str]
    bash_max_output_bytes: int
    bash_max_argv_count: int
    bash_max_argv_bytes: int
    bash_max_script_bytes: int
    bash_max_stdin_bytes: int
    bash_artifact_dir: str
    bash_artifact_ttl_seconds: int
    bash_output_dir: str
    bash_output_ttl_seconds: int
    bash_max_output_file_bytes: int
    server_log_enabled: bool
    server_log_level: str
    server_log_dir: str


def load_app_config(project_root: Path = PROJECT_ROOT) -> AppConfig:
    env_config = load_env_config(project_root)

    default_cwd = env_config.get("DEFAULT_CWD", None)
    if default_cwd:
        default_cwd = os.path.abspath(os.path.expandvars(os.path.expanduser(default_cwd)))

    return AppConfig(
        project_root=project_root,
        env_config=env_config,
        transport=env_config.get("TRANSPORT", "stdio").lower(),
        sse_port=int(env_config.get("SSE_PORT", "8000")),
        sse_host=env_config.get("SSE_HOST", "localhost"),
        default_cwd=default_cwd,
        bash_max_output_bytes=parse_int_env(
            env_config.get("BASH_MAX_OUTPUT_BYTES"), 8192, min_value=1
        ),
        bash_max_argv_count=parse_int_env(
            env_config.get("BASH_MAX_ARGV_COUNT"), 4096, min_value=1
        ),
        bash_max_argv_bytes=parse_int_env(
            env_config.get("BASH_MAX_ARGV_BYTES"), 262144, min_value=1
        ),
        bash_max_script_bytes=parse_int_env(
            env_config.get("BASH_MAX_SCRIPT_BYTES"), 1048576, min_value=1
        ),
        bash_max_stdin_bytes=parse_int_env(
            env_config.get("BASH_MAX_STDIN_BYTES"), 4194304, min_value=1
        ),
        bash_artifact_dir=os.path.abspath(
            os.path.expandvars(
                os.path.expanduser(
                    env_config.get(
                        "BASH_ARTIFACT_DIR",
                        os.path.join("/tmp", "mcp-server-bash", "artifacts"),
                    )
                )
            )
        ),
        bash_artifact_ttl_seconds=parse_int_env(
            env_config.get("BASH_ARTIFACT_TTL_SECONDS"), 86400, min_value=1
        ),
        bash_output_dir=os.path.abspath(
            os.path.expandvars(
                os.path.expanduser(
                    env_config.get(
                        "BASH_OUTPUT_DIR",
                        os.path.join("/tmp", "mcp-server-bash", "outputs"),
                    )
                )
            )
        ),
        bash_output_ttl_seconds=parse_int_env(
            env_config.get("BASH_OUTPUT_TTL_SECONDS"), 604800, min_value=1
        ),
        bash_max_output_file_bytes=parse_int_env(
            env_config.get("BASH_MAX_OUTPUT_FILE_BYTES"), 104857600, min_value=1
        ),
        server_log_enabled=parse_bool_env(env_config.get("SERVER_LOG_ENABLED"), True),
        server_log_level=env_config.get("SERVER_LOG_LEVEL", "INFO").strip().upper(),
        server_log_dir=os.path.abspath(
            os.path.expandvars(
                os.path.expanduser(
                    env_config.get("SERVER_LOG_DIR", os.path.join(os.getcwd(), "logs", "server"))
                )
            )
        ),
    )


def setup_server_logger(*, enabled: bool, level: str, log_dir: str) -> tuple[logging.Logger, Optional[str]]:
    logger = logging.getLogger("mcp_server_bash")
    if logger.handlers:
        existing_path = None
        for handler in logger.handlers:
            existing_path = getattr(handler, "baseFilename", existing_path)
        return logger, existing_path

    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.propagate = False

    if not enabled:
        logger.addHandler(logging.NullHandler())
        return logger, None

    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(
        log_dir,
        f"mcp-server-{datetime.now().strftime('%Y-%m-%d')}.log",
    )
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(getattr(logging, level, logging.INFO))
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)
    return logger, log_file
