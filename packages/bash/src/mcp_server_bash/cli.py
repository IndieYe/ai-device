"""Optional MCP CLI entrypoint; importing the package does not load FastMCP."""


def main():
    try:
        from .server import main as server_main
    except ModuleNotFoundError as exc:
        if exc.name in {"fastmcp", "mcp", "pydantic"}:
            raise SystemExit(
                "MCP dependencies are not installed. Use uv sync --extra mcp "
                "or install 'mcp-server-bash[mcp]'."
            ) from exc
        raise
    return server_main()
