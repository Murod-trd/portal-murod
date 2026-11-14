import hmac
import ipaddress
import time
import uuid
from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse

class PortalMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        request.request_id = uuid.uuid4().hex
        if settings.PRODUCTION and not request.path.startswith('/api/internal/'):
            address=request.META.get('REMOTE_ADDR','')
            loopback_health=request.path in {'/api/health/live','/api/health/ready'} and address in {'127.0.0.1','::1'}
            token=request.headers.get('X-Portal-Proxy-Key','')
            valid=len(settings.EDGE_PROXY_TOKEN)>=40 and hmac.compare_digest(token.encode(),settings.EDGE_PROXY_TOKEN.encode())
            if not loopback_health and not valid:
                return self.secure_response(request,JsonResponse({'code':'NOT_FOUND'},status=404))
            if valid:
                try:
                    request.META['PORTAL_CLIENT_IP']=str(ipaddress.ip_address(request.headers.get('X-Forwarded-For','')))
                except ValueError:
                    return self.secure_response(request,JsonResponse({'code':'INVALID_PROXY_HEADER'},status=400))
        if request.path.startswith('/api/') and any('\x00' in text for key,values in request.GET.lists() for text in [key,*values]):
            return self.secure_response(request,JsonResponse({'code':'VALIDATION_ERROR'},status=400))
        if request.path.startswith('/api/') and not request.path.startswith('/api/internal/'):
            if request.method not in {'GET','HEAD','OPTIONS'} and (request.user.is_authenticated or request.path=='/api/v1/auth/login'):
                if request.headers.get('Origin') not in settings.CSRF_TRUSTED_ORIGINS:
                    return self.secure_response(request,JsonResponse({'code':'ORIGIN_REJECTED'}, status=403))
            if request.user.is_authenticated:
                now = time.time()
                last = request.session.get('last_seen_at', now)
                created = request.session.get('login_at', now)
                if now-last > settings.SESSION_IDLE_SECONDS or now-created > settings.SESSION_COOKIE_AGE:
                    logout(request)
                    return self.secure_response(request,JsonResponse({'code':'SESSION_EXPIRED'}, status=401))
                request.session['last_seen_at'] = now
        response = self.get_response(request)
        return self.secure_response(request,response)

    @staticmethod
    def secure_response(request,response):
        response['X-Request-ID'] = request.request_id
        response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response['Cache-Control'] = 'private, no-store'
        response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
        return response
