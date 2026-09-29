import logging

from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .errors import PlannerError

log = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """Every error leaves as {"error": {"code", "message", ...}}; nothing internal leaks."""
    if isinstance(exc, PlannerError):
        return Response({"error": {"code": exc.code, "message": exc.message, **exc.extra}}, status=exc.status)
    response = exception_handler(exc, context)
    if response is None:
        log.exception("unhandled error")
        return Response({"error": {"code": "internal_error", "message": "Unexpected error."}}, status=500)
    if isinstance(exc, exceptions.ValidationError):
        error = {"code": "invalid_request", "message": "Invalid query parameters.", "fields": response.data}
    elif isinstance(exc, exceptions.Throttled):
        error = {"code": "throttled", "message": "Too many requests. Please slow down."}
    else:
        error = {"code": "error", "message": str(exc.detail) if hasattr(exc, "detail") else "Error."}
    response.data = {"error": error}
    return response
