"""Simplified audio-project three-way merger.

Public API:

- :func:`load_project` / :func:`normalize_project`
- :func:`merge_projects`
- :class:`Session`
"""

from .normalize import load_project, normalize_project, ProjectFormatError
from .engine import merge_projects, MISSING, DELETED
from .session import Session, SessionError, UnresolvedConflictsError

__all__ = [
    "load_project",
    "normalize_project",
    "ProjectFormatError",
    "merge_projects",
    "MISSING",
    "DELETED",
    "Session",
    "SessionError",
    "UnresolvedConflictsError",
]

__version__ = "1.0.0"
