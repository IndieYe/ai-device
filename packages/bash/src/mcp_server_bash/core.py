"""Public local execution API, importable without the optional MCP dependencies.

ExecutionRuntime owns execution and background-result lifetimes. Managed file
helpers support script/stdin without mixing shell source and stdin data.
"""

from .artifacts import create_script_artifacts, create_stdin_artifact
from .models import ExecutionArtifacts, ExecutionRequest, ExecutionTarget
from .shell import ExecutionRuntime

__all__ = [
    "ExecutionArtifacts", "ExecutionRequest", "ExecutionRuntime", "ExecutionTarget",
    "create_script_artifacts", "create_stdin_artifact",
]
