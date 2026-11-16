import logging
from django.http import JsonResponse
from django.db import IntegrityError, OperationalError
from django.core.exceptions import ValidationError
from rest_framework.exceptions import APIException, ValidationError as ApiValidationError

class PortalError(APIException):
    def __init__(self, code, status=400, details=None):
        self.status_code = status
        self.detail = {'code': code, **({'details': details} if details is not None else {})}

def error_codes(detail):
    """Machine-readable twin of DRF validation details: the same shape with each message replaced by its code."""
    if isinstance(detail, dict):
        return {key: error_codes(value) for key, value in detail.items()}
    if isinstance(detail, (list, tuple)):
        return [error_codes(value) for value in detail]
    return getattr(detail, 'code', None) or 'invalid'

def exception_handler(exc, context):
    from rest_framework.views import exception_handler as drf_exception_handler
    response = drf_exception_handler(exc, context)
    if response is not None:
        if isinstance(exc, PortalError):
            return response
        if response.status_code in (401, 403):
            code = 'CSRF_REJECTED' if 'CSRF' in str(getattr(exc, 'detail', '')) else ('AUTH_REQUIRED' if not context['request'].user.is_authenticated else 'FORBIDDEN')
            response.data = {'code': code}
        elif isinstance(response.data, dict) and 'code' not in response.data:
            response.data = {'code': 'VALIDATION_ERROR', 'details': response.data, **({'fieldCodes': error_codes(exc.detail)} if isinstance(exc, ApiValidationError) else {})}
        return response
    if isinstance(exc, IntegrityError):
        from rest_framework.response import Response
        return Response({'code': 'CONFLICT'}, status=409)
    if isinstance(exc,ValidationError):
        from rest_framework.response import Response
        return Response({'code':'VALIDATION_ERROR'},status=400)
    from rest_framework.response import Response
    logging.getLogger('portal').exception('Request failed: %s', type(exc).__name__, exc_info=exc)
    return Response({'code': 'DATABASE_UNAVAILABLE' if isinstance(exc, OperationalError) else 'INTERNAL_ERROR'}, status=503 if isinstance(exc, OperationalError) else 500)

def csrf_failure(request, reason=''):
    return JsonResponse({'code': 'CSRF_REJECTED'}, status=403)
