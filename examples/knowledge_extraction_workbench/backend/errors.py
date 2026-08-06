"""Stable API errors for the workbench."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

LOGGER = logging.getLogger("knowledge_extraction_workbench")


class WorkbenchError(Exception):
    """An expected error with a stable, caller-facing code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.details = details or {}


def error_payload(error: WorkbenchError, request_id: str) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.message,
        "retryable": error.retryable,
        "request_id": request_id,
        "details": error.details,
    }


@web.middleware
async def error_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request["request_id"] = request_id
    try:
        response = await handler(request)
    except WorkbenchError as error:
        response = web.json_response(error_payload(error, request_id), status=error.status)
    except web.HTTPException as http_error:
        if not request.path.startswith("/api/"):
            raise
        code = "REQUEST_TOO_LARGE" if http_error.status == 413 else f"HTTP_{http_error.status}"
        message = "上传内容超过服务限制。" if http_error.status == 413 else "API 路径或请求方法无效。"
        error = WorkbenchError(code, message, status=http_error.status)
        response = web.json_response(error_payload(error, request_id), status=http_error.status)
    except Exception:
        LOGGER.exception("Unhandled workbench request failure request_id=%s path=%s", request_id, request.path)
        error = WorkbenchError(
            "INTERNAL_ERROR",
            "服务处理请求失败，请稍后重试。",
            status=500,
            retryable=True,
        )
        response = web.json_response(error_payload(error, request_id), status=500)
    response.headers["X-Request-ID"] = request_id
    return response
