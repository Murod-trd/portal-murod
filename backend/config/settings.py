"""Python backend. Production configuration fails closed on unsafe defaults."""
import json
import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parents[2]
RUNTIME_DIR = Path(os.environ.get('PORTAL_RUNTIME_DIR', str(BASE_DIR / '.runtime'))).resolve()
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
if (RUNTIME_DIR / 'config.json').exists():
    for key, value in json.loads((RUNTIME_DIR / 'config.json').read_text()).items():
        os.environ.setdefault(key, str(value))
APP_ENV = os.environ.get('APP_ENV', 'demo')
if APP_ENV not in {'demo','test','production'}:raise RuntimeError('Unknown APP_ENV; production must be spelled explicitly.')
PRODUCTION = APP_ENV == 'production'
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if len(SECRET_KEY) < 40:
    raise RuntimeError('Run python scripts/setup.py or supply DJANGO_SECRET_KEY (40+ random characters).')
DEBUG = False
APP_ORIGIN = os.environ.get('APP_ORIGIN', 'http://localhost:3000')
ALLOWED_HOSTS = ['localhost', '127.0.0.1'] + ([] if PRODUCTION else ['testserver'])
ALLOWED_HOSTS.extend(filter(None, os.environ.get('ALLOWED_HOSTS', urlparse(APP_ORIGIN).hostname or '').split(',')))
CSRF_TRUSTED_ORIGINS = [APP_ORIGIN]
if not PRODUCTION and (RUNTIME_DIR / 'preview-origin.txt').exists():
    origin = (RUNTIME_DIR / 'preview-origin.txt').read_text().strip().rstrip('/')
    parsed = urlparse(origin)
    if parsed.scheme in ('http', 'https') and parsed.hostname:
        CSRF_TRUSTED_ORIGINS.append(origin)
        ALLOWED_HOSTS.append(parsed.hostname)

INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
    'django.contrib.staticfiles', 'rest_framework', 'drf_spectacular',
    'django_otp', 'django_otp.plugins.otp_totp', 'portal',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'portal.middleware.PortalMiddleware',
]
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True}]
DB_ENGINE = os.environ.get('DB_ENGINE', 'sqlite')
if DB_ENGINE not in {'sqlite','postgres'}:raise RuntimeError('Unknown database engine.')
if DB_ENGINE == 'postgres':
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': os.environ.get('POSTGRES_DB', 'portal'),
        'USER': os.environ.get('POSTGRES_USER', 'portal'), 'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
        'HOST': os.environ.get('POSTGRES_HOST', 'postgres'), 'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': 60, 'CONN_HEALTH_CHECKS': True,
        'OPTIONS': {'connect_timeout': 5, 'options': '-c statement_timeout=10000 -c lock_timeout=5000'},
    }}
else:
    # A development fallback, NEVER presented as a test of PostgreSQL.
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': RUNTIME_DIR / 'demo.sqlite3', 'OPTIONS': {'timeout': 20, 'transaction_mode': 'IMMEDIATE', 'init_command': 'PRAGMA journal_mode=WAL;'}}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
PASSWORD_HASHERS = ['django.contrib.auth.hashers.Argon2PasswordHasher', 'django.contrib.auth.hashers.PBKDF2PasswordHasher']
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_NAME = 'ti_session'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = PRODUCTION or os.environ.get('SECURE_COOKIES') == 'true'
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_TTL_SECONDS', 43200))
SESSION_IDLE_SECONDS = int(os.environ.get('SESSION_IDLE_SECONDS', 43200))
CSRF_COOKIE_NAME = 'ti_csrf'
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_FAILURE_VIEW = 'portal.errors.csrf_failure'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_SSL_REDIRECT = PRODUCTION
SECURE_HSTS_SECONDS = 31536000 if PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = PRODUCTION
SECURE_HSTS_PRELOAD = False
# Internal office/VPN hosts are deliberately not submitted to the public preload list.
SILENCED_SYSTEM_CHECKS = ['security.W021']
X_FRAME_OPTIONS = 'DENY'
# Only set this when the proxy removes untrusted incoming forwarded headers.
if os.environ.get('TRUST_HTTPS_PROXY') == 'true':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
LANGUAGE_CODE = 'uz'
TIME_ZONE = 'Asia/Tashkent'
USE_TZ = True
USE_I18N = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_URL = '/assets/'
STATIC_ROOT = BASE_DIR / 'frontend-dist' / 'assets'
WHITENOISE_ROOT = BASE_DIR / 'frontend-dist'
WHITENOISE_AUTOREFRESH = not PRODUCTION
MEDIA_ROOT = Path(os.environ.get('PRIVATE_STORAGE_DIR',str(RUNTIME_DIR / 'private-files')))
STORAGE_DRIVER = os.environ.get('STORAGE_DRIVER', 'local')
if STORAGE_DRIVER not in {'local','filesystem','s3'}:
    raise RuntimeError('Unknown storage driver.')
EDGE_PROXY_TOKEN = os.environ.get('EDGE_PROXY_TOKEN','')
if STORAGE_DRIVER == 's3':
    STORAGES = {'default': {'BACKEND': 'storages.backends.s3.S3Storage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
    AWS_STORAGE_BUCKET_NAME = os.environ.get('S3_BUCKET', 'portal-private')
    AWS_S3_ENDPOINT_URL = os.environ.get('S3_ENDPOINT')
    AWS_ACCESS_KEY_ID = os.environ.get('S3_ACCESS_KEY')
    AWS_SECRET_ACCESS_KEY = os.environ.get('S3_SECRET_KEY')
    AWS_S3_REGION_NAME = os.environ.get('S3_REGION', 'us-east-1')
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    AWS_S3_FILE_OVERWRITE = False
MAX_UPLOAD_BYTES = int(os.environ.get('MAX_UPLOAD_BYTES', 20 * 1024 * 1024))
MAX_UPLOAD_TOTAL_BYTES = int(os.environ.get('MAX_UPLOAD_TOTAL_BYTES', 2 * 1024 * 1024 * 1024))
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 150
DATA_UPLOAD_MAX_NUMBER_FILES = 1
REQUIRE_MFA = os.environ.get('REQUIRE_MFA', 'none')
if REQUIRE_MFA not in {'none','privileged','all'}:
    raise RuntimeError('Unknown MFA policy.')
REQUIRE_PASSWORD_CHANGE = os.environ.get('REQUIRE_PASSWORD_CHANGE', 'false') == 'true'
OTP_TOTP_ISSUER = 'Toshkent Invest'
OTP_TOTP_THROTTLE_FACTOR = 2
WORKER_TOKEN = os.environ.get('WORKER_TOKEN', '')
CLAMAV_HOST = os.environ.get('CLAMAV_HOST', '')
CLAMAV_PORT = int(os.environ.get('CLAMAV_PORT', 3310))
OCR_LANGUAGES = os.environ.get('OCR_LANGUAGES', 'Latin+uzb_cyrl+rus+eng+tur')
MAX_OCR_PAGES = int(os.environ.get('MAX_OCR_PAGES', 80))
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['portal.permissions.PortalSessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['portal.permissions.PortalAccess'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PARSER_CLASSES': ['portal.parsers.ObjectJSONParser'],
    'URL_FORMAT_OVERRIDE': None,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'portal.errors.exception_handler',
    'UNAUTHENTICATED_USER': 'django.contrib.auth.models.AnonymousUser',
}
SPECTACULAR_SETTINGS = {
    'ENUM_NAME_OVERRIDES': {'PortalLocaleEnum': ['uz','ru','en']},'TITLE': 'Toshkent Invest Portal API', 'VERSION': (BASE_DIR/'RELEASE').read_text().strip(), 'SERVE_INCLUDE_SCHEMA': False, 'COMPONENT_SPLIT_REQUEST': True}
LOGGING = {'version': 1, 'disable_existing_loggers': False, 'handlers': {'console': {'class': 'logging.StreamHandler'}}, 'root': {'handlers': ['console'], 'level': 'WARNING'}}
if PRODUCTION:
    if DB_ENGINE != 'postgres' or STORAGE_DRIVER not in {'s3','filesystem'} or not APP_ORIGIN.startswith('https://') or len(WORKER_TOKEN)<40 or not CLAMAV_HOST or not os.environ.get('POSTGRES_PASSWORD') or len(EDGE_PROXY_TOKEN)<40:
        raise RuntimeError('Production requires PostgreSQL, private storage, exact HTTPS origin, proxy/worker tokens and ClamAV.')
    if STORAGE_DRIVER=='filesystem' and (not os.environ.get('PRIVATE_STORAGE_DIR') or not MEDIA_ROOT.is_absolute() or MEDIA_ROOT.is_relative_to(BASE_DIR/'frontend-dist')):
        raise RuntimeError('Private storage must be an explicit absolute directory outside static files.')
    if STORAGE_DRIVER=='s3' and os.environ.get('S3_ENDPOINT', 'https://s3.amazonaws.com').startswith('http://'):
        raise RuntimeError('Production object storage requires HTTPS.')
    if '*' in ALLOWED_HOSTS:
        raise RuntimeError('Wildcard ALLOWED_HOSTS is forbidden in production.')
