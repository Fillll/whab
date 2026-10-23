"""Only authored error messages reach the browser."""

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from whab.domain.errors import StoreError
from whab.plugins import BankError, PluginError
from whab.plugins.effect_store import EffectError


def install_error_handlers(app):
    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse({"error": "Invalid request"}, status_code=400)

    async def authored_error(_request, error):
        return JSONResponse({"error": str(error)}, status_code=400)

    for error_type in (StoreError, BankError, PluginError):
        app.add_exception_handler(error_type, authored_error)

    @app.exception_handler(EffectError)
    async def effect_error(_request, error):
        return JSONResponse({"error": str(error), "code": error.code}, status_code=409)

    @app.exception_handler(Exception)
    async def safe_error(_request, _error):
        return JSONResponse(
            {"error": "Request could not complete. Check your local configuration and retry."},
            status_code=500,
        )
