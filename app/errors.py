"""Доменні помилки та єдиний конверт відповіді.

Назовні ніколи не летить stacktrace, тіло помилки провайдера чи фрагмент ключа.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    code = "INTERNAL_ERROR"
    status_code = 500
    message = "Internal error"

    def __init__(self, message: str | None = None, details: dict | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details or {}
        self.headers: dict[str, str] = {}


class SessionNotFoundError(AppError):
    code = "SESSION_NOT_FOUND"
    status_code = 404
    message = "Session not found"


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422
    message = "Invalid input"


class UnknownModelError(AppError):
    code = "UNKNOWN_MODEL"
    status_code = 400
    message = "Model is not present in the pricing catalog"


class ContextTooLongError(AppError):
    code = "CONTEXT_TOO_LONG"
    status_code = 400
    message = "Context does not fit into the model limit"


class LLMRateLimitedError(AppError):
    code = "LLM_RATE_LIMITED"
    status_code = 429
    message = "Upstream model provider rate limit"

    def __init__(self, message: str | None = None, retry_after: int | None = None):
        super().__init__(message)
        if retry_after is not None:
            self.headers["Retry-After"] = str(retry_after)


class LLMUnavailableError(AppError):
    code = "LLM_UNAVAILABLE"
    status_code = 504
    message = "Model provider is unavailable"


class LLMConfigError(AppError):
    code = "LLM_CONFIG_ERROR"
    status_code = 502
    message = "Model provider is not configured correctly"


class StorageUnavailableError(AppError):
    code = "STORAGE_UNAVAILABLE"
    status_code = 503
    message = "Storage is unavailable"


def _envelope(request: Request, code: str, message: str, details: dict, status: int,
              headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
        headers={"X-Request-ID": getattr(request.state, "request_id", ""), **(headers or {})},
    )


def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return _envelope(request, exc.code, exc.message, exc.details, exc.status_code, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        details = {"fields": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]}
        return _envelope(request, "VALIDATION_ERROR", "Invalid input", details, 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return _envelope(request, code, str(exc.detail), {}, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # ponytail: деталі невідомої помилки лише в лог, назовні — голий код
        log.exception("unhandled error request_id=%s", getattr(request.state, "request_id", ""))
        return _envelope(request, "INTERNAL_ERROR", "Internal error", {}, 500)
