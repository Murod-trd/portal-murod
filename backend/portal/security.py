import hashlib
import hmac
from contextlib import contextmanager
from datetime import timedelta
from django.conf import settings
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone
from .errors import PortalError
from .models import AuditLog, RateLimit, WriteLock

def audit(user, action, entity, entity_id='', fields=None, outcome='SUCCESS', metadata=None):
    AuditLog.objects.create(actor=user if user and user.is_authenticated else None, action=action, entity_type=entity, entity_id=str(entity_id)[:80], changed_fields=list(fields or []), outcome=outcome, metadata=metadata or {})

@contextmanager
def domain_write():
    """Serializes graph/import writes; PostgreSQL lock also protects concurrent cycles."""
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [784731])
        else:
            WriteLock.objects.get_or_create(name='domain')
            WriteLock.objects.filter(name='domain').update(revision=F('revision') + 1)
        yield

def throttle(key, limit, seconds):
    digest = hmac.new(settings.SECRET_KEY.encode(), key.encode(), hashlib.sha256).hexdigest()
    now = timezone.now()
    with transaction.atomic():
        row, created = RateLimit.objects.get_or_create(key=digest, defaults={'count': 1, 'expires_at': now + timedelta(seconds=seconds)})
        if not created:
            row = RateLimit.objects.select_for_update().get(pk=digest)
            if row.expires_at <= now:
                row.count, row.expires_at = 1, now + timedelta(seconds=seconds)
            else:
                row.count += 1
            row.save(update_fields=['count','expires_at'])
        count = row.count
    if count > limit:
        raise PortalError('RATE_LIMITED', 429)

def refund_throttle(key):
    """Gives back one unit of a still-open window (a successful login must not use the failure budget)."""
    digest = hmac.new(settings.SECRET_KEY.encode(), key.encode(), hashlib.sha256).hexdigest()
    RateLimit.objects.filter(key=digest, expires_at__gt=timezone.now(), count__gt=0).update(count=F('count') - 1)

def managed(user):
    return user.portal_account.role in {'HR','ADMIN'}
