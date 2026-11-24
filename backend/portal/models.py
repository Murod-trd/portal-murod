import uuid
from django.conf import settings
from django.db import models
from django.contrib.postgres.indexes import GinIndex
from .domain import normalize

class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True

class Office(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200, db_index=True)
    active = models.BooleanField(default=True)
    def save(self, *args, **kwargs):
        self.normalized_name = normalize(self.name)[:200]  # NFKC may lengthen text (№ -> No)
        super().save(*args, **kwargs)

class OrgUnit(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200, db_index=True)
    type = models.CharField(max_length=50, default='DEPARTMENT')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    head = models.ForeignKey('Employee', null=True, blank=True, on_delete=models.SET_NULL, related_name='headed_units')
    active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    def save(self, *args, **kwargs):
        self.normalized_name = normalize(self.name)[:200]  # NFKC may lengthen text (№ -> No)
        super().save(*args, **kwargs)

class EmployeeNumber(models.Model):
    # A separate monotonically increasing sequence: numbers are not reused on archive.
    id = models.BigAutoField(primary_key=True)

class Employee(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_number = models.CharField(max_length=32, unique=True, editable=False)
    display_name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200, db_index=True)
    position_title = models.CharField(max_length=200)
    normalized_position = models.CharField(max_length=200, db_index=True)
    unit = models.ForeignKey(OrgUnit, on_delete=models.PROTECT, related_name='employees')
    office = models.ForeignKey(Office, on_delete=models.PROTECT, related_name='employees')
    manager = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reports')
    extension = models.CharField(max_length=30, blank=True, default='')
    work_email = models.EmailField(blank=True, default='')
    mobile_phone = models.CharField(max_length=40, blank=True, default='')
    mobile_visible = models.BooleanField(default=True)
    birthday = models.DateField(null=True, blank=True)
    birthday_visible = models.BooleanField(default=True)
    photo = models.ForeignKey('FileObject', on_delete=models.SET_NULL, null=True, blank=True, related_name='employee_photos')
    floor = models.CharField(max_length=30, blank=True, default='')
    room = models.CharField(max_length=40, blank=True, default='')
    status = models.CharField(max_length=16, choices=[('ACTIVE','ACTIVE'),('ARCHIVED','ARCHIVED')], default='ACTIVE', db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    def save(self, *args, **kwargs):
        self.normalized_name, self.normalized_position = normalize(self.display_name)[:200], normalize(self.position_title)[:200]
        if not self.employee_number:
            self.employee_number = f'TI-{EmployeeNumber.objects.create().id:06d}'
        super().save(*args, **kwargs)

class UserAccount(Timestamped):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE, related_name='portal_account')
    role = models.CharField(max_length=16, choices=[('EMPLOYEE','EMPLOYEE'),('HR','HR'),('ADMIN','ADMIN')], default='EMPLOYEE')
    display_name = models.CharField(max_length=200)
    employee = models.OneToOneField(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='account')
    must_change_password = models.BooleanField(default=True)
    locale = models.CharField(max_length=2, choices=[('uz','uz'),('ru','ru'),('en','en')], default='uz')
    session_version = models.PositiveIntegerField(default=1)
    blocked_at = models.DateTimeField(null=True, blank=True)

class FileObject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to='quarantine/%Y/%m/')
    original_name = models.CharField(max_length=200)
    mime_type = models.CharField(max_length=120)
    size_bytes = models.PositiveBigIntegerField()
    checksum = models.CharField(max_length=64)
    purpose = models.CharField(max_length=16, choices=[(x,x) for x in ['DOCUMENT','PHOTO','COVER','IMPORT']])
    security_status = models.CharField(max_length=16, default='QUARANTINED', choices=[(x,x) for x in ['QUARANTINED','SCANNING','APPROVED','REJECTED','SCAN_ERROR']], db_index=True)
    scanner_version = models.CharField(max_length=150, blank=True)
    signature_version = models.CharField(max_length=150, blank=True)
    scanned_at = models.DateTimeField(null=True)
    rejection_code = models.CharField(max_length=80, blank=True)
    parsed_data = models.JSONField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class News(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=300)
    normalized_title = models.CharField(max_length=300, db_index=True)
    summary = models.CharField(max_length=1000, blank=True, default='')
    body = models.TextField()
    language = models.CharField(max_length=2, default='uz')
    translations = models.JSONField(default=dict, blank=True)
    category = models.CharField(max_length=80, default='NEWS')
    cover = models.ForeignKey(FileObject, null=True, blank=True, on_delete=models.SET_NULL, related_name='news_covers')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, default='DRAFT', choices=[('DRAFT','DRAFT'),('PUBLISHED','PUBLISHED')])
    pinned = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    def save(self, *args, **kwargs):
        self.normalized_title = normalize(self.title)[:300]
        super().save(*args, **kwargs)

class NewsView(models.Model):
    news = models.ForeignKey(News, on_delete=models.CASCADE, related_name='views')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    first_viewed_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['news','user'], name='unique_news_view')]

class NewsRevision(models.Model):
    news = models.ForeignKey(News, on_delete=models.CASCADE, related_name='revisions')
    snapshot = models.JSONField()
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class Document(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=300)
    normalized_title = models.CharField(max_length=300, db_index=True)
    number = models.CharField(max_length=80, blank=True, default='')
    document_date = models.DateField(null=True, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    category = models.CharField(max_length=80, default='OTHER', db_index=True)
    description = models.TextField(blank=True, default='')
    language = models.CharField(max_length=2, default='uz')
    translations = models.JSONField(default=dict, blank=True)
    owner_unit = models.ForeignKey(OrgUnit, null=True, blank=True, on_delete=models.SET_NULL)
    current_version = models.ForeignKey('DocumentVersion', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    status = models.CharField(max_length=16, default='DRAFT', choices=[('DRAFT','DRAFT'),('PUBLISHED','PUBLISHED')])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    class Meta:
        indexes = [GinIndex(fields=['normalized_title'], name='portal_doc_title_trgm', opclasses=['gin_trgm_ops'])]
    def save(self, *args, **kwargs):
        self.normalized_title = normalize(self.title)[:300]
        super().save(*args, **kwargs)

class DocumentVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='versions')
    label = models.CharField(max_length=80)
    file = models.ForeignKey(FileObject, on_delete=models.PROTECT, related_name='document_versions')
    document_date = models.DateField(null=True, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    change_note = models.CharField(max_length=1000, blank=True, default='')
    extraction_status = models.CharField(max_length=16, default='PENDING', choices=[(x,x) for x in ['PENDING','PROCESSING','READY','ERROR']])
    extracted_text = models.TextField(blank=True, default='')
    normalized_text = models.TextField(blank=True, default='')
    ocr_quality = models.JSONField(default=dict, blank=True)
    extraction_error = models.CharField(max_length=80, blank=True, default='')
    pages = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['document','label'], name='unique_document_version')]
        indexes = [GinIndex(fields=['normalized_text'], name='portal_doc_text_trgm', opclasses=['gin_trgm_ops'])]

class ProcessingJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(FileObject, on_delete=models.CASCADE, related_name='jobs')
    kind = models.CharField(max_length=16, choices=[(x,x) for x in ['SCAN','EXTRACT','PARSE_IMPORT']])
    status = models.CharField(max_length=16, default='PENDING', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    lease_hash = models.CharField(max_length=64, blank=True)
    lease_expires = models.DateTimeField(null=True)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['file','kind'], name='unique_processing_job')]

class ImportJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(FileObject, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, default='PREVIEW')
    rows = models.JSONField()
    errors = models.JSONField(default=list)
    preview_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True)
    report = models.JSONField(null=True)

class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=40)
    entity_id = models.CharField(max_length=80, blank=True)
    outcome = models.CharField(max_length=16, default='SUCCESS')
    changed_fields = models.JSONField(default=list)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes = [models.Index(fields=['entity_type','entity_id','created_at'])]

class RateLimit(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()

class WriteLock(models.Model):
    name = models.CharField(primary_key=True, max_length=32)
    revision = models.PositiveBigIntegerField(default=0)


class WorkerHeartbeat(models.Model):
    name = models.CharField(primary_key=True,max_length=32,default='file-worker')
    seen_at = models.DateTimeField()
    capabilities = models.JSONField(default=dict)
