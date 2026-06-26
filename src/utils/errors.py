"""
Utility functions for error handling.

Mirrors src/utils/errors.ts from the TypeScript version.
"""
from __future__ import annotations

from typing import Any, Optional


def get_error_code(error: Any) -> Optional[str]:
    if hasattr(error, "errno"):
        return str(error.errno)
    if isinstance(error, OSError):
        return error.errno
    return None


def is_enoent_error(error: Any) -> bool:
    if isinstance(error, FileNotFoundError):
        return True
    if isinstance(error, OSError) and error.errno == 2:
        return True
    return False