"""Local filesystem core with an optional MCP server adapter."""


def create_mcp():
    """Create the MCP adapter on demand; requires the package's ``mcp`` extra."""
    from .server import create_mcp as factory

    return factory()


__all__ = ["create_mcp"]
