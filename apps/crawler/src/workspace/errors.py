"""Workspace exception hierarchy.

All workspace errors inherit from ``WorkspaceError`` so the top-level
CLI boundary can catch them uniformly and translate to user-facing
error messages + exit code 1.
"""

from __future__ import annotations


class WorkspaceError(Exception):
    """Base class for all workspace errors."""


# ── CSV tool errors ────────────────────────────────────────────────────


class CsvToolError(WorkspaceError):
    """Error raised by CSV management operations."""


class InvalidSlugError(CsvToolError):
    """Slug does not match the required format."""


class SlugNotFoundError(CsvToolError):
    """Company slug not found in companies.csv."""


class DuplicateSlugError(CsvToolError):
    """Company slug already exists when uniqueness is required."""


class BoardNotFoundError(CsvToolError):
    """Board row not found in boards.csv."""


class NothingToUpdateError(CsvToolError):
    """Entity exists but no fields were provided to update."""


class MissingRequiredFieldError(CsvToolError):
    """A required field (e.g. board_url) was not supplied."""


# ── Git / GitHub errors ───────────────────────────────────────────────


class GitError(WorkspaceError):
    """Base class for git/gh subprocess failures."""


class GitCommandError(GitError):
    """A ``git`` subprocess exited with a non-zero code."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git command failed (exit {returncode}): {' '.join(cmd)}\n{stderr}")


class GitHubApiError(GitError):
    """A ``gh`` CLI subprocess exited with a non-zero code."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"gh command failed (exit {returncode}): {' '.join(cmd)}\n{stderr}")


# ── Other workspace errors ────────────────────────────────────────────


class WorkspaceStateError(WorkspaceError):
    """Workspace state is inconsistent or missing."""


class CommandAbortedError(WorkspaceError):
    """A command was aborted by the user or a precondition check."""
