"""Error types for apply_patch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class PatchError(Exception):
    """Structured patch application error."""

    code: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the error to a serializable dictionary."""
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
