"""Domain exceptions shared by every layer.

Services raise these; `exceptions/handlers.py` maps each class to one HTTP
status, and the agent's tools turn them into error results for the model.
`code` is a stable machine-readable identifier sent in every error body;
messages are written for the end user.
"""

from typing import ClassVar


class RestaurantError(Exception):
    """Base class for every error this application raises on purpose."""

    code: ClassVar[str] = "INTERNAL_ERROR"


# --- Identity and access ---


class AuthenticationError(RestaurantError):
    """Credentials are missing or wrong."""

    code = "UNAUTHENTICATED"

    def __init__(self, message: str = "Valid credentials are required.") -> None:
        super().__init__(message)


class PermissionDeniedError(RestaurantError):
    """The caller is signed in but their role doesn't allow the operation."""

    code = "FORBIDDEN"

    def __init__(self, message: str = "Administrator access is required.") -> None:
        super().__init__(message)


class RateLimitedError(RestaurantError):
    code = "RATE_LIMITED"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            f"Too many requests. Try again in {retry_after_seconds} seconds."
        )
        self.retry_after_seconds = retry_after_seconds


# --- Missing records ---


class ResourceNotFoundError(RestaurantError):
    """No such record, or one the caller may not see (reported identically, so
    the existence of other users' records isn't revealed)."""

    code = "NOT_FOUND"

    def __init__(self, resource: str, resource_id: int) -> None:
        super().__init__(f"{resource} {resource_id} not found.")
        self.resource = resource
        self.resource_id = resource_id


# --- Business rule conflicts ---


class BusinessConflictError(RestaurantError):
    """The request is well-formed but conflicts with the current state."""

    code = "CONFLICT"


class ItemUnavailableError(BusinessConflictError):
    code = "ITEM_UNAVAILABLE"

    def __init__(self, item_id: int, title: str) -> None:
        super().__init__(f"{title} (item {item_id}) is not available right now.")
        self.item_id = item_id


class InsufficientCapacityError(BusinessConflictError):
    code = "INSUFFICIENT_CAPACITY"

    def __init__(self, item_id: int, title: str, requested: int, available: int):
        super().__init__(
            f"Only {available} portion(s) of {title} (item {item_id}) are "
            f"available; {requested} requested."
        )
        self.item_id = item_id
        self.requested = requested
        self.available = available


class InvalidStatusTransitionError(BusinessConflictError):
    code = "INVALID_TRANSITION"

    def __init__(self, order_id: int, current: str, target: str) -> None:
        super().__init__(
            f"Order {order_id} is {current}; it can't be changed to {target}."
        )
        self.order_id = order_id
        self.current = current
        self.target = target


class IdempotencyConflictError(BusinessConflictError):
    code = "IDEMPOTENCY_CONFLICT"

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            f"Idempotency key {idempotency_key!r} was already used for a "
            "different order. Use a new key for a new order."
        )
        self.idempotency_key = idempotency_key
