from .contracts import response_for
from drf_spectacular.utils import extend_schema, OpenApiTypes
import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from . import models as m
from .domain import search_fold
from .errors import PortalError
from .security import audit, throttle, managed, domain_write
from .services import get_or_error

def valid_ocr_quality(value):
    import math
    if not isinstance(value,dict) or set(value)-{'engine','ocrPages','meanConfidence','lowConfidenceLines','profiles','rotatedPages','sparsePages','embeddedImages','rotations'}:
        return False
    if 'engine' in value and (not isinstance(value['engine'],str) or len(value['engine'])>80):
        return False
    for key in ['ocrPages','lowConfidenceLines','rotatedPages','sparsePages','embeddedImages']:
        if key in value and (type(value[key]) is not int or not 0<=value[key]<=400000):
            return False
    confidence=value.get('meanConfidence')
    if confidence is not None and (type(confidence) not in {int,float} or not math.isfinite(confidence) or not 0<=confidence<=100):
        return False
    rotations=value.get('rotations',[])
    if not isinstance(rotations,list) or len(rotations)>200 or any(type(v) is not int or v not in {0,90,180,270} for v in rotations):
        return False
    profiles=value.get('profiles',[])
    return isinstance(profiles,list) and len(profiles)<=12 and all(isinstance(v,str) and len(v)<=150 for v in profiles)


def file_state(obj):
    parse_error=obj.jobs.filter(kind='PARSE_IMPORT',status='ERROR').first()
    return {'id':str(obj.id),'fileName':obj.original_name,'mimeType':obj.mime_type,'fileSize':obj.size_bytes,'securityStatus':obj.security_status,'rejectionCode':obj.rejection_code,'importReady':obj.parsed_data is not None,'processingError':parse_error.error_code if parse_error else ''}

class UploadView(APIView):
    required_roles={'HR','ADMIN'}
    parser_classes=[MultiPartParser,FormParser]
    @extend_schema(operation_id='UploadView_post', request=OpenApiTypes.OBJECT, responses=response_for('UploadView','post'))
    def post(self,request):
        throttle(f'upload:{request.user.pk}',20,60)
        purpose=request.data.get('purpose','DOCUMENT')
        if purpose not in {'DOCUMENT','PHOTO','COVER','IMPORT'}:
            raise PortalError('INVALID_PURPOSE')
        upload=request.FILES.get('file')
        if not upload or upload.size==0 or upload.size>settings.MAX_UPLOAD_BYTES:
            raise PortalError('UPLOAD_SIZE_LIMIT')
        name=Path(upload.name.replace('\\','/')).name.replace('\r','').replace('\n','')[:180]
        allowed={'DOCUMENT':{'.pdf','.docx','.jpg','.jpeg','.png','.tif','.tiff'},'IMPORT':{'.csv','.xlsx'},'PHOTO':{'.jpg','.jpeg','.png','.webp'},'COVER':{'.jpg','.jpeg','.png','.webp'}}
        if Path(name).suffix.lower() not in allowed[purpose]:
            raise PortalError('UNSUPPORTED_FORMAT')
        digest=hashlib.sha256()
        for chunk in upload.chunks():
            digest.update(chunk)
        upload.seek(0)
        obj=m.FileObject(original_name=name,mime_type='application/octet-stream',size_bytes=upload.size,checksum=digest.hexdigest(),purpose=purpose,created_by=request.user)
        obj.file.save(f'{uuid.uuid4().hex}.upload',upload,save=False)
        try:
            # The quota check and reservation serialize; concurrent uploads cannot overbook.
            from django.db.models import Sum
            with domain_write():
                total=m.FileObject.objects.filter(created_by=request.user).aggregate(n=Sum('size_bytes'))['n'] or 0
                if total+upload.size>settings.MAX_UPLOAD_TOTAL_BYTES:
                    raise PortalError('UPLOAD_QUOTA',429)
                obj.save()
                m.ProcessingJob.objects.create(file=obj,kind='SCAN')
                audit(request.user,'FILE_QUARANTINED','FILE',obj.id,['securityStatus'])
        except Exception:
            # Storage is outside the database transaction: undo an uncommitted upload.
            try:
                obj.file.delete(save=False)
            except Exception:
                logging.getLogger('portal').error('Upload storage cleanup failed: file_id=%s',obj.id)
            raise
        return Response(file_state(obj),status=202)

class FileStateView(APIView):
    required_roles={'HR','ADMIN'}
    @extend_schema(operation_id='FileStateView_get', request=OpenApiTypes.OBJECT, responses=response_for('FileStateView','get'))
    def get(self,request,id):
        return Response(file_state(get_or_error(m.FileObject.objects,pk=id)))
    @extend_schema(operation_id='FileStateView_post', request=OpenApiTypes.OBJECT, responses=response_for('FileStateView','post'))
    def post(self,request,id):
        with domain_write():
            obj=get_or_error(m.FileObject.objects.select_for_update(),pk=id)
            if obj.security_status not in {'SCAN_ERROR','REJECTED'} or obj.rejection_code=='DELETED':
                raise PortalError('INVALID_FILE_STATE',409)
            if obj.rejection_code=='MALWARE_DETECTED':
                raise PortalError('MALWARE_REUPLOAD_REQUIRED',409)
            obj.security_status='QUARANTINED'
            obj.rejection_code=''
            obj.save(update_fields=['security_status','rejection_code'])
            m.ProcessingJob.objects.update_or_create(file=obj,kind='SCAN',defaults={'status':'PENDING','attempts':0,'error_code':''})
            audit(request.user,'SCAN_RETRY','FILE',obj.id)
        return Response(file_state(obj))

def authorize_download(obj,user):
    if obj.security_status!='APPROVED':
        raise PortalError('FILE_NOT_APPROVED',403)
    # Import sources are parsed as data, never offered as executable spreadsheet downloads.
    if obj.purpose=='IMPORT':
        raise PortalError('FILE_NOT_AVAILABLE',403)
    if managed(user):
        return
    if obj.purpose=='PHOTO' and obj.employee_photos.filter(status='ACTIVE').exists():
        return
    if obj.purpose=='COVER' and obj.news_covers.filter(status='PUBLISHED',deleted_at=None).filter(Q(published_at=None)|Q(published_at__lte=timezone.now())).filter(Q(expires_at=None)|Q(expires_at__gt=timezone.now())).exists():
        return
    if obj.purpose=='DOCUMENT' and obj.document_versions.filter(document__status='PUBLISHED',document__deleted_at=None).exists():
        return
    raise PortalError('FILE_NOT_AVAILABLE',403)

class FileDownloadView(APIView):
    @extend_schema(operation_id='FileDownloadView_get', responses=response_for('FileDownloadView','get'))
    def get(self,request,id):
        obj=get_or_error(m.FileObject.objects,pk=id)
        authorize_download(obj,request.user)
        throttle(f'download:{request.user.pk}',120,60)
        inline=obj.purpose in {'PHOTO','COVER'} or (obj.mime_type in {'application/pdf','image/jpeg','image/png'} and request.query_params.get('inline')=='true')
        response=FileResponse(obj.file.open('rb'),as_attachment=not inline,filename=obj.original_name,content_type=obj.mime_type)
        response['X-Content-Type-Options']='nosniff'
        response['X-Frame-Options']='SAMEORIGIN'
        return response

class ReprocessView(APIView):
    required_roles={'HR','ADMIN'}
    @extend_schema(operation_id='ReprocessView_post', request=OpenApiTypes.OBJECT, responses=response_for('ReprocessView','post'))
    def post(self,request,id,version_id):
        version=get_or_error(m.DocumentVersion.objects.select_related('file'),pk=version_id,document_id=id,document__deleted_at=None)
        if version.file.security_status!='APPROVED':
            raise PortalError('FILE_NOT_APPROVED',403)
        with domain_write():
            version=get_or_error(m.DocumentVersion.objects.select_for_update().select_related('file'),pk=version_id,document_id=id,document__deleted_at=None,file__security_status='APPROVED')
            job=m.ProcessingJob.objects.select_for_update().filter(file=version.file,kind='EXTRACT').first()
            if job and job.status in {'PENDING','RUNNING'}:
                raise PortalError('PROCESSING_IN_PROGRESS',409)
            # A file can be reused by several versions; their extraction state is shared.
            m.DocumentVersion.objects.filter(file=version.file).update(extraction_status='PENDING',extraction_error='')
            m.ProcessingJob.objects.update_or_create(file=version.file,kind='EXTRACT',defaults={'status':'PENDING','attempts':0,'error_code':'','lease_hash':'','lease_expires':None,'finished_at':None})
            audit(request.user,'EXTRACTION_RETRY','DOCUMENT',id)
        return Response({'success':True})

class WorkerView(APIView):
    parser_classes=[JSONParser]
    authentication_classes=[]
    permission_classes=[AllowAny]
    def worker_access(self,request):
        header=request.headers.get('Authorization','')
        expected='Bearer '+settings.WORKER_TOKEN
        if len(settings.WORKER_TOKEN)<40 or not hmac.compare_digest(header.encode(),expected.encode()):
            raise PortalError('WORKER_AUTH_REQUIRED',401)
    def lease(self,request,id):
        value=request.headers.get('Authorization','').removeprefix('Bearer ')
        job=get_or_error(m.ProcessingJob.objects.select_related('file'),pk=id,status='RUNNING')
        if job.file.rejection_code=='DELETED' or not job.lease_expires or job.lease_expires<=timezone.now() or not hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(),job.lease_hash):
            raise PortalError('LEASE_EXPIRED',403)
        return job

class WorkerClaimView(WorkerView):
    @extend_schema(exclude=True)
    def post(self,request):
        self.worker_access(request)
        now=timezone.now()
        heartbeat=m.WorkerHeartbeat.objects.filter(name='file-worker').first()
        if not heartbeat or (now-heartbeat.seen_at).total_seconds()>15:
            m.WorkerHeartbeat.objects.update_or_create(name='file-worker',defaults={'seen_at':now})
        for file in m.FileObject.objects.filter(security_status='REJECTED',rejection_code='DELETED')[:10]:
            if file.document_versions.exists() or file.news_covers.exists() or file.employee_photos.exists():
                continue
            try:
                file.file.delete(save=False)
                file.delete()
            except Exception:
                # Keep a blocked durable marker for retry; never restore download access.
                audit(None,'FILE_PURGE_RETRY','FILE',file.id,outcome='FAILED')
        with domain_write():
            m.ProcessingJob.objects.filter(status='RUNNING',lease_expires__lt=now,attempts__lt=4).update(status='PENDING')
            for stale in m.ProcessingJob.objects.filter(status='RUNNING',lease_expires__lt=now,attempts__gte=4):
                stale.status,stale.error_code='ERROR','WORKER_RETRY_LIMIT'
                stale.save(update_fields=['status','error_code'])
                if stale.kind=='SCAN':
                    m.FileObject.objects.filter(pk=stale.file_id).update(security_status='SCAN_ERROR',rejection_code='WORKER_RETRY_LIMIT')
                elif stale.kind=='EXTRACT':
                    m.DocumentVersion.objects.filter(file_id=stale.file_id).update(extraction_status='ERROR',extraction_error='WORKER_RETRY_LIMIT')
            m.RateLimit.objects.filter(expires_at__lt=now-timedelta(days=1)).delete()
            job=m.ProcessingJob.objects.select_for_update().select_related('file').filter(status='PENDING',attempts__lt=4).order_by('created_at').first()
            if not job:
                return Response({'job':None})
            if job.file.rejection_code=='DELETED' or (job.kind=='SCAN' and job.file.security_status not in {'QUARANTINED','SCANNING'}) or (job.kind!='SCAN' and job.file.security_status!='APPROVED'):
                job.status='CANCELLED'
                job.save(update_fields=['status'])
                return Response({'job':None})
            lease=secrets.token_urlsafe(32)
            job.status,job.started_at,job.lease_expires='RUNNING',now,now+timedelta(minutes=15)
            job.attempts+=1
            job.lease_hash=hashlib.sha256(lease.encode()).hexdigest()
            job.save()
            if job.kind=='SCAN':
                job.file.security_status='SCANNING'
                job.file.save(update_fields=['security_status'])
            elif job.kind=='EXTRACT':
                m.DocumentVersion.objects.filter(file=job.file).update(extraction_status='PROCESSING',ocr_quality={})
            return Response({'job':{'id':str(job.id),'kind':job.kind,'lease':lease,'fileName':job.file.original_name,'purpose':job.file.purpose,'checksum':job.file.checksum,'size':job.file.size_bytes}})

class WorkerHeartbeatView(WorkerView):
    @extend_schema(exclude=True)
    def post(self,request):
        self.worker_access(request)
        languages=request.data.get('ocrLanguages',[])
        if not isinstance(languages,list) or len(languages)>100 or any(not isinstance(value,str) or len(value)>64 for value in languages):
            raise PortalError('INVALID_WORKER_CAPABILITIES')
        m.WorkerHeartbeat.objects.update_or_create(name='file-worker',defaults={'seen_at':timezone.now(),'capabilities':{'ocrLanguages':languages,'processorSandbox':request.data.get('processorSandbox') is True}})
        return Response({'success':True})

class WorkerFileView(WorkerView):
    @extend_schema(exclude=True)
    def get(self,request,id):
        job=self.lease(request,id)
        if job.kind!='SCAN' and job.file.security_status!='APPROVED':
            raise PortalError('FILE_NOT_APPROVED',403)
        return FileResponse(job.file.file.open('rb'),content_type='application/octet-stream',as_attachment=True,filename='input.bin')

class WorkerResultView(WorkerView):
    @extend_schema(exclude=True)
    def post(self,request,id):
        job=self.lease(request,id)
        result=request.data
        if not isinstance(result,dict):
            raise PortalError('INVALID_RESULT')
        error=str(result.get('error',''))[:80]
        with domain_write():
            job=get_or_error(m.ProcessingJob.objects.select_for_update().select_related('file'),pk=id,status='RUNNING',lease_hash=job.lease_hash)
            file=job.file
            if not job.lease_expires or job.lease_expires<=timezone.now() or file.rejection_code=='DELETED':
                raise PortalError('LEASE_EXPIRED',403)
            if error:
                job.status,job.error_code='ERROR',error
                if job.kind=='SCAN':
                    file.security_status='SCAN_ERROR' if error.startswith('ANTIVIRUS') else 'REJECTED'
                    file.rejection_code=error
                    file.save(update_fields=['security_status','rejection_code'])
                elif job.kind=='EXTRACT':
                    m.DocumentVersion.objects.filter(file=file).update(extraction_status='ERROR',extraction_error=error)
                audit(None,'PROCESSING_FAILED','FILE',file.id,outcome='FAILED',metadata={'code':error,'kind':job.kind})
            elif job.kind=='SCAN':
                if result.get('status')!='APPROVED' or not result.get('scannerVersion') or not result.get('signatureVersion'):
                    raise PortalError('INCOMPLETE_SCAN_RESULT')
                allowed_mime={'DOCUMENT':{'application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document','image/jpeg','image/png','image/tiff'},'IMPORT':{'text/csv','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},'PHOTO':{'image/jpeg'},'COVER':{'image/jpeg'}}
                if result.get('mimeType') not in allowed_mime[file.purpose]:
                    raise PortalError('INVALID_FILE_TYPE')
                if result.get('cleanedBase64'):
                    if file.purpose not in {'PHOTO','COVER'}:
                        raise PortalError('INVALID_DERIVATIVE')
                    try:
                        cleaned=base64.b64decode(result['cleanedBase64'],validate=True)
                    except (ValueError,TypeError):
                        raise PortalError('INVALID_DERIVATIVE')
                    if len(cleaned)>settings.MAX_UPLOAD_BYTES or not cleaned.startswith(b'\xff\xd8\xff'):
                        raise PortalError('INVALID_DERIVATIVE')
                    old_name=file.file.name
                    file.file.save(f'{uuid.uuid4().hex}.jpg',ContentFile(cleaned),save=False)
                    file.size_bytes=len(cleaned)
                    file.checksum=hashlib.sha256(cleaned).hexdigest()
                    transaction.on_commit(lambda: file.file.storage.delete(old_name))
                file.security_status='APPROVED'
                file.mime_type=str(result.get('mimeType','application/octet-stream'))[:120]
                file.scanner_version=str(result['scannerVersion'])[:150]
                file.signature_version=str(result['signatureVersion'])[:150]
                file.scanned_at=timezone.now()
                file.rejection_code=''
                file.save()
                job.status='DONE'
                if file.purpose=='IMPORT':
                    m.ProcessingJob.objects.update_or_create(file=file,kind='PARSE_IMPORT',defaults={'status':'PENDING','attempts':0})
                audit(None,'FILE_SCAN_PASSED','FILE',file.id,['securityStatus'])
            elif job.kind=='EXTRACT':
                text=result.get('text')
                if not isinstance(text,str) or len(text)>2_000_000 or file.security_status!='APPROVED':
                    raise PortalError('INVALID_EXTRACTION_RESULT')
                search_text=result.get('searchText','')
                quality=result.get('quality',{})
                if not isinstance(search_text,str) or len(search_text)>2_000_000 or not valid_ocr_quality(quality):
                    raise PortalError('INVALID_EXTRACTION_RESULT')
                pages=result.get('pages')
                if pages is not None and (type(pages) is not int or pages>settings.MAX_OCR_PAGES or pages<0):
                    raise PortalError('PAGE_LIMIT')
                # Trash is reversible: finish indexing its existing versions as well.
                m.DocumentVersion.objects.filter(file=file).update(extraction_status='READY',extraction_error='',extracted_text=text,normalized_text=search_fold(text+'\n'+search_text),pages=pages,ocr_quality=quality)
                job.status='DONE'
            elif job.kind=='PARSE_IMPORT':
                if not isinstance(result.get('headers'),list) or not isinstance(result.get('rows'),list) or len(result['rows'])>2000 or len(result['headers'])>50:
                    raise PortalError('INVALID_IMPORT_RESULT')
                numbers=result.get('rowNumbers')
                if numbers is not None and (not isinstance(numbers,list) or len(numbers)!=len(result['rows']) or any(type(v) is not int or v<2 for v in numbers) or numbers!=sorted(set(numbers))):
                    raise PortalError('INVALID_IMPORT_RESULT')
                file.parsed_data={'headers':result['headers'],'rows':result['rows'],**({'rowNumbers':numbers} if numbers is not None else {})}
                file.save(update_fields=['parsed_data'])
                job.status='DONE'
            job.finished_at=timezone.now()
            job.lease_hash=''
            job.save()
        return Response({'success':True})
