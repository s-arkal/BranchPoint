"""BranchPoint exceptions."""


class BranchPointError(Exception):
    """Base BranchPoint exception."""


class NoActiveTraceError(BranchPointError):
    """Raised when recording requires an active trace."""


class TraceNotFoundError(BranchPointError):
    """Raised when a requested trace run does not exist."""
