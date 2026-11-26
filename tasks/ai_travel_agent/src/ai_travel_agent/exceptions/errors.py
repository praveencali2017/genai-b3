"""Domain exceptions shared by every layer.

Services raise these; `exceptions/handlers.py` maps each class to one HTTP
status, and the agent's tools turn them into error results for the model.
`code` is a stable machine-readable identifier sent in every error body;
messages are written for the end user.
"""

from typing import ClassVar


class TravelError(Exception):
    """Base class for every error this application raises on purpose."""

    code: ClassVar[str] = "INTERNAL_ERROR"


# --- Identity and access ---


class AuthenticationError(TravelError):
    """Credentials are missing or wrong."""

    code = "UNAUTHENTICATED"

    def __init__(self, message: str = "Valid credentials are required.") -> None:
        super().__init__(message)


class PermissionDeniedError(TravelError):
    """The caller is signed in but their role doesn't allow the operation."""

    code = "FORBIDDEN"

    def __init__(self, message: str = "Administrator access is required.") -> None:
        super().__init__(message)


class RateLimitedError(TravelError):
    code = "RATE_LIMITED"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            f"Too many requests. Try again in {retry_after_seconds} seconds."
        )
        self.retry_after_seconds = retry_after_seconds


# --- Missing records ---


class ResourceNotFoundError(TravelError):
    """No such record, or one the caller may not see (reported identically, so
    the existence of other users' records isn't revealed)."""

    code = "NOT_FOUND"

    def __init__(self, resource: str, resource_id: int) -> None:
        super().__init__(f"{resource} {resource_id} not found.")
        self.resource = resource
        self.resource_id = resource_id


# --- Business rule conflicts ---


class BusinessConflictError(TravelError):
    """The request is well-formed but conflicts with the current state."""

    code = "CONFLICT"


class PackageUnavailableError(BusinessConflictError):
    code = "PACKAGE_UNAVAILABLE"

    def __init__(self, package_id: int, title: str) -> None:
        super().__init__(
            f"{title} (package {package_id}) is not available for booking."
        )
        self.package_id = package_id


class DepartureInPastError(BusinessConflictError):
    """The package has already departed (or departs today), so it can't be
    booked or cancelled any more."""

    code = "DEPARTURE_PASSED"

    def __init__(self, package_id: int, title: str, start_date: str) -> None:
        super().__init__(
            f"{title} (package {package_id}) departs on {start_date}; "
            "only future departures can be booked or cancelled."
        )
        self.package_id = package_id


class InsufficientCapacityError(BusinessConflictError):
    code = "INSUFFICIENT_CAPACITY"

    def __init__(self, package_id: int, title: str, requested: int, available: int):
        super().__init__(
            f"Only {available} seat(s) are left on {title} (package {package_id}); "
            f"{requested} requested."
        )
        self.package_id = package_id
        self.requested = requested
        self.available = available


class InvalidStatusTransitionError(BusinessConflictError):
    code = "INVALID_TRANSITION"

    def __init__(self, booking_id: int, current: str, target: str) -> None:
        super().__init__(
            f"Booking {booking_id} is {current}; it can't be changed to {target}."
        )
        self.booking_id = booking_id
        self.current = current
        self.target = target


class IdempotencyConflictError(BusinessConflictError):
    code = "IDEMPOTENCY_CONFLICT"

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            f"Idempotency key {idempotency_key!r} was already used for a "
            "different booking. Use a new key for a new booking."
        )
        self.idempotency_key = idempotency_key


class TermsChangedError(BusinessConflictError):
    """A pending booking's price, seats or refund changed before confirmation."""

    code = "TERMS_CHANGED"


# --- Uploaded documents ---


class InvalidDocumentError(TravelError):
    """An uploaded file can't be indexed: empty, damaged or without text."""

    code = "INVALID_DOCUMENT"


class UnsupportedFileError(InvalidDocumentError):
    code = "UNSUPPORTED_FILE"


class FileTooLargeError(InvalidDocumentError):
    code = "FILE_TOO_LARGE"

    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"The file is {size_bytes / 1_048_576:.1f} MB; "
            f"the limit is {limit_bytes / 1_048_576:.0f} MB."
        )


class DuplicateDocumentError(BusinessConflictError):
    code = "DUPLICATE_DOCUMENT"

    def __init__(self, filename: str, version: int) -> None:
        super().__init__(
            f"This exact file is already indexed as {filename} (version {version})."
        )


# --- AI dependencies (language model, embeddings, vector store) ---


class AiServiceError(TravelError):
    """An AI dependency failed; the request can be retried later."""

    code = "AI_UNAVAILABLE"


class AssistantNotConfiguredError(AiServiceError):
    code = "AI_NOT_CONFIGURED"


class AssistantUnavailableError(AiServiceError):
    """The language-model or embedding API call failed or timed out."""


class VectorStoreError(AiServiceError):
    code = "VECTOR_STORE_UNAVAILABLE"

    def __init__(self, operation: str, cause: Exception) -> None:
        super().__init__(f"The document index could not {operation}: {cause}")
