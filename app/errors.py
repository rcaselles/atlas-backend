from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_CODES = {400: "bad_request", 404: "not_found", 409: "conflict", 422: "invalid_request", 500: "internal_error"}


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        self.status, self.message, self.code = status, message, code or _CODES.get(status, "error")


def _body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError):
        return JSONResponse(_body(exc.code, exc.message), status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(_body(_CODES.get(exc.status_code, "error"), str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return JSONResponse(_body("invalid_request", msg), status_code=422)
