"""Typed API errors. The `reason` is machine-readable for the voice agent."""

from __future__ import annotations

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status_code: int, reason: str, message: str | None = None) -> None:
        super().__init__(status_code=status_code, detail={"reason": reason, "message": message or reason})
        self.reason = reason


def conflict(reason: str, message: str | None = None) -> ApiError:
    return ApiError(409, reason, message)


def not_found(what: str) -> ApiError:
    return ApiError(404, f"{what}_not_found")
