"""Environment variable names owned by the Bash MCP service itself.

Kept in a standalone module so both the configuration layer and the execution
backend can share one list without the core depending on the MCP adapter.
"""

from __future__ import annotations

# Names ``load_env_config`` may read from the process environment.
#
# The same set doubles as the leak boundary: the execution backend inherits the
# server's own environment for every spawned command, so these values would
# otherwise be visible to ``bash``, ``run`` and ``script`` output. Excluding them
# keeps service configuration and caller secrets out of user commands.
SERVICE_CONFIG_ENV_KEYS = frozenset(
    {
        "TRANSPORT",
        "SSE_HOST",
        "SSE_PORT",
        "DEFAULT_CWD",
        "BASH_MAX_OUTPUT_BYTES",
        "BASH_MAX_ARGV_COUNT",
        "BASH_MAX_ARGV_BYTES",
        "BASH_MAX_SCRIPT_BYTES",
        "BASH_MAX_STDIN_BYTES",
        "BASH_ARTIFACT_DIR",
        "BASH_ARTIFACT_TTL_SECONDS",
        "BASH_OUTPUT_DIR",
        "BASH_OUTPUT_TTL_SECONDS",
        "BASH_MAX_OUTPUT_FILE_BYTES",
        "SERVER_LOG_ENABLED",
        "SERVER_LOG_LEVEL",
        "SERVER_LOG_DIR",
        "BASH_CALLER_CONTEXT_TOKEN",
    }
)
