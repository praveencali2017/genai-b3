"""Domain error -> HTTP response, with one body shape for every error.

Every error response is `{"code", "message", "request_id"}` (plus `details` for
validation errors), and carries an `X-Request-ID` header matching the logs.
Routes contain no try/except: each domain error class maps to one status here.
Unexpected exceptions are logged with their traceback and answered with a
generic 500 that exposes nothing internal.
"""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_restaurant_ordering_assistant.exceptions.errors import (
    AiServiceError,
    AuthenticationError,
    BusinessConflictError,
    FileTooLargeError,
    InvalidDocumentError,
    PermissionDeniedError,
    RateLimitedError,
    ResourceNotFoundError,
    RestaurantError,
    UnsupportedFileError,
)
from ai_restaurant_ordering_assistant.schemas.error import ErrorResponse

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
# A client-supplied request id is reused only if it is short and harmless.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Starlette picks the handler of the most specific registered class, so the
# base class is only the fallback for an error without a mapping of its own.
ERROR_STATUS: dict[type[RestaurantError], int] = {
    RestaurantError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    ResourceNotFoundError: status.HTTP_404_NOT_FOUND,
    BusinessConflictError: status.HTTP_409_CONFLICT,
    RateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    InvalidDocumentError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnsupportedFileError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    FileTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    # An AI dependency being down is "try again later", not a server bug.
    AiServiceError: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def register_error_handling(app: FastAPI) -> None:
    app.middleware("http")(_assign_request_id)
    for error_class in ERROR_STATUS:
        app.add_exception_handler(error_class, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex


async def _assign_request_id(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request.state.request_id = (
        incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
    )
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request.state.request_id
    return response


async def _handle_domain_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RestaurantError)
    status_code = _status_for(error)
    if isinstance(error, AiServiceError):
        logger.warning("Request %s: %s", request_id_of(request), error)
    elif status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error(
            "Request %s failed: %s", request_id_of(request), error, exc_info=error
        )
    headers: dict[str, str] = {}
    if isinstance(error, AuthenticationError):
        headers["WWW-Authenticate"] = 'Basic realm="restaurant"'
    if isinstance(error, RateLimitedError):
        headers["Retry-After"] = str(error.retry_after_seconds)
    return _error_response(request, status_code, error.code, str(error), headers)


async def _handle_validation_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    details = [
        {
            "location": ".".join(str(part) for part in item.get("loc", ())),
            "message": item.get("msg", ""),
        }
        for item in error.errors()
    ]
    return _error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "VALIDATION_ERROR",
        "The request is invalid; see details.",
        details=details,
    )


async def _handle_http_exception(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    return _error_response(
        request,
        error.status_code,
        f"HTTP_{error.status_code}",
        str(error.detail),
        dict(error.headers or {}),
    )


async def _handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    logger.error(
        "Unhandled error in request %s", request_id_of(request), exc_info=error
    )
    return _error_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "INTERNAL_ERROR",
        "Something went wrong on our side. Quote the request id if you report it.",
    )


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    request_id = request_id_of(request)
    body = ErrorResponse(
        code=code, message=message, request_id=request_id, details=details
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body, exclude_none=True),
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


def _status_for(error: RestaurantError) -> int:
    for error_class in type(error).__mro__:
        if error_class in ERROR_STATUS:
            return ERROR_STATUS[error_class]
    return status.HTTP_500_INTERNAL_SERVER_ERROR
