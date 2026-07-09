"""BranchPoint exceptions."""


class BranchPointError(Exception):
    """Base BranchPoint exception."""


class NoActiveTraceError(BranchPointError):
    """Raised when recording requires an active trace."""


class TraceNotFoundError(BranchPointError):
    """Raised when a requested trace run does not exist."""


class EventContractError(BranchPointError, ValueError):
    """Raised when a run, event, or edge violates the trace contract."""
