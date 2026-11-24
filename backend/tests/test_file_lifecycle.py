"""Lifecycle/concurrency regressions. APPROVED fixtures are not antivirus evidence."""
import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.utils import timezone

from portal import models as m,services
from portal.file_views import WorkerView
from .test_content_and_files import approved_fixture,create_doc

pytestmark=pytest.mark.django_db


def upload(api,data=b'%PDF-1.7\n'+b'x'*40):
    return api.client.post('/api/v1/files',{'file':SimpleUploadedFile('sample.pdf',data,content_type='application/pdf'),'purpose':'DOCUMENT'},HTTP_ORIGIN=settings.APP_ORIGIN,HTTP_X_CSRFTOKEN=api.token)


def running_job(file,kind='EXTRACT',expired=False):
    token='test-lease-token-not-for-production'
    job=m.ProcessingJob.objects.create(file=file,kind=kind,status='RUNNING',attempts=1,lease_hash=hashlib.sha256(token.encode()).hexdigest(),lease_expires=timezone.now()+timedelta(minutes=-1 if expired else 10))
    return job,token


def result(api,job,token,data):
    return api.client.post(f'/api/internal/worker/{job.id}/result',data,content_type='application/json',HTTP_AUTHORIZATION='Bearer '+token)


def test_upload_quota_rejection_cleans_storage(hr,settings):
    settings.MAX_UPLOAD_TOTAL_BYTES=80
    api=hr[2]
    assert upload(api).status_code==202
    response=upload(api)
    assert response.status_code==429 and response.json()['code']=='UPLOAD_QUOTA'
    assert m.FileObject.objects.count()==1
    assert m.ProcessingJob.objects.count()==1
    assert len([p for p in Path(settings.MEDIA_ROOT).rglob('*') if p.is_file()])==1


def test_database_failure_rolls_back_upload_and_removes_bytes(hr,settings,monkeypatch):
    def unavailable(*args,**kwargs):
        raise IntegrityError('synthetic rollback test')
    monkeypatch.setattr(m.ProcessingJob.objects,'create',unavailable)
    response=upload(hr[2])
    assert response.status_code==409
    assert not m.FileObject.objects.exists()
    assert not m.ProcessingJob.objects.exists()
    assert not [p for p in Path(settings.MEDIA_ROOT).rglob('*') if p.is_file()]


def test_permanent_delete_revokes_running_worker_lease(hr):
    doc,old,_=create_doc(hr)
    job,token=running_job(old.file)
    api=hr[2]
    api.post(f'/api/v1/content/documents/{doc.id}/trash',{})
    assert api.delete(f'/api/v1/trash/documents/{doc.id}',{'confirmation':doc.title}).status_code==200
    job.refresh_from_db();old.file.refresh_from_db()
    assert job.status=='CANCELLED' and not job.lease_hash and job.lease_expires is None
    assert old.file.rejection_code=='DELETED'
    assert result(api,job,token,{'text':'late worker must not restore deleted content','pages':1}).status_code in {403,404}
    assert api.get(f'/api/v1/files/{old.file_id}/download').status_code==403


def test_deleting_one_document_keeps_shared_file_and_lease(hr):
    doc,old,_=create_doc(hr)
    second=m.Document.objects.create(title='Shared file test',created_by=hr[0],status='PUBLISHED')
    version=m.DocumentVersion.objects.create(document=second,label='1',file=old.file,created_by=hr[0])
    second.current_version=version;second.save()
    job,token=running_job(old.file)
    api=hr[2]
    api.post(f'/api/v1/content/documents/{doc.id}/trash',{})
    assert api.delete(f'/api/v1/trash/documents/{doc.id}',{'confirmation':doc.title}).status_code==200
    job.refresh_from_db();old.file.refresh_from_db()
    assert old.file.security_status=='APPROVED' and job.status=='RUNNING'
    assert result(api,job,token,{'text':'shared file remains available','pages':1}).status_code==200
    version.refresh_from_db()
    assert version.extraction_status=='READY' and version.extracted_text=='shared file remains available'


@pytest.mark.parametrize('kind',['version','cover','photo'])
def test_file_revoked_between_validation_and_transaction_cannot_be_attached(hr,monkeypatch,kind):
    user,employee,api=hr
    doc,_,current=create_doc(hr)
    file=approved_fixture(user,purpose={'version':'DOCUMENT','cover':'COVER','photo':'PHOTO'}[kind])
    original=services.validated
    def validate_then_revoke(*args,**kwargs):
        values=original(*args,**kwargs)
        m.FileObject.objects.filter(pk=file.pk).update(security_status='REJECTED',rejection_code='DELETED')
        return values
    monkeypatch.setattr(services,'validated',validate_then_revoke)
    if kind=='version':
        response=api.post(f'/api/v1/documents/{doc.id}/versions',{'label':'3','fileId':str(file.pk)})
    elif kind=='cover':
        response=api.post('/api/v1/news',{'title':'Race test','body':'<p>Content</p>','coverFileId':str(file.pk)})
    else:
        response=api.patch(f'/api/v1/employees/{employee.id}',{'photoFileId':str(file.pk)})
    assert response.status_code==409 and response.json()['code']=='FILE_NOT_APPROVED'
    assert not m.DocumentVersion.objects.filter(file=file).exists()
    assert not m.News.objects.filter(cover=file).exists()
    assert not m.Employee.objects.filter(photo=file).exists()
    doc.refresh_from_db()
    assert doc.current_version_id==current.pk


def test_archived_reference_is_rechecked_before_employee_write(hr,monkeypatch):
    user,employee,api=hr
    manager=m.Employee.objects.create(display_name='Manager race fixture',position_title='Manager',unit=employee.unit,office=employee.office)
    original=services.validated
    def validate_then_archive(*args,**kwargs):
        values=original(*args,**kwargs)
        m.Employee.objects.filter(pk=manager.pk).update(status='ARCHIVED')
        return values
    monkeypatch.setattr(services,'validated',validate_then_archive)
    response=api.patch(f'/api/v1/employees/{employee.id}',{'managerId':str(manager.pk)})
    assert response.status_code==409 and response.json()['code']=='REFERENCE_CHANGED'
    employee.refresh_from_db()
    assert employee.manager_id is None


def test_new_version_does_not_invalidate_running_extraction(hr):
    doc,_,current=create_doc(hr)
    job,token=running_job(current.file)
    api=hr[2]
    response=api.post(f'/api/v1/documents/{doc.id}/versions',{'label':'3','fileId':str(current.file_id)})
    assert response.status_code==201,response.content
    job.refresh_from_db()
    assert job.status=='RUNNING' and job.lease_hash==hashlib.sha256(token.encode()).hexdigest()
    assert result(api,job,token,{'text':'one extraction for shared bytes','pages':1}).status_code==200
    assert m.DocumentVersion.objects.filter(pk=response.json()['id'],extraction_status='READY').exists()


def test_manual_reprocess_does_not_overwrite_running_job(hr):
    doc,_,current=create_doc(hr)
    job,token=running_job(current.file)
    response=hr[2].post(f'/api/v1/documents/{doc.id}/versions/{current.pk}/reprocess',{})
    assert response.status_code==409 and response.json()['code']=='PROCESSING_IN_PROGRESS'
    job.refresh_from_db()
    assert job.lease_hash==hashlib.sha256(token.encode()).hexdigest()


def test_expired_scan_lease_is_retried_with_fresh_token(hr):
    file=approved_fixture(hr[0]);file.security_status='SCANNING';file.save()
    job,old_token=running_job(file,'SCAN',expired=True)
    api=hr[2]
    response=api.client.post('/api/internal/worker/claim',{},content_type='application/json',HTTP_AUTHORIZATION='Bearer '+settings.WORKER_TOKEN)
    new=response.json()['job']
    assert new and new['id']==str(job.id) and new['lease']!=old_token
    job.refresh_from_db();file.refresh_from_db()
    assert job.status=='RUNNING' and job.attempts==2 and file.security_status=='SCANNING'
    assert result(api,job,old_token,{'error':'ANTIVIRUS_UNAVAILABLE'}).status_code==403
    assert result(api,job,new['lease'],{'error':'ANTIVIRUS_UNAVAILABLE'}).status_code==200


def test_result_lease_is_rechecked_after_waiting_for_transaction(hr,monkeypatch):
    file=approved_fixture(hr[0]);file.security_status='SCANNING';file.save()
    job,token=running_job(file,'SCAN')
    original=WorkerView.lease
    def expire_after_initial_check(self,request,id):
        valid=original(self,request,id)
        m.ProcessingJob.objects.filter(pk=id).update(lease_expires=timezone.now()-timedelta(seconds=1))
        return valid
    monkeypatch.setattr(WorkerView,'lease',expire_after_initial_check)
    response=result(hr[2],job,token,{'status':'APPROVED','mimeType':'application/pdf','scannerVersion':'FIXTURE','signatureVersion':'FIXTURE'})
    assert response.status_code==403
    file.refresh_from_db()
    assert file.security_status=='SCANNING'

def test_current_manager_graph_is_used_after_validation(hr,monkeypatch):
    _,employee,api=hr
    manager=m.Employee.objects.create(display_name='Graph race fixture',position_title='Manager',unit=employee.unit,office=employee.office)
    original=services.validated
    def changed_graph(*args,**kwargs):
        values=original(*args,**kwargs)
        m.Employee.objects.filter(pk=manager.pk).update(manager=employee)
        return values
    monkeypatch.setattr(services,'validated',changed_graph)
    response=api.patch(f'/api/v1/employees/{employee.id}',{'managerId':str(manager.pk)})
    assert response.status_code==400 and response.json()['code']=='MANAGER_CYCLE'
    employee.refresh_from_db()
    assert employee.manager_id is None


def test_current_unit_graph_is_used_after_validation(hr,monkeypatch):
    _,employee,api=hr
    parent=m.OrgUnit.objects.create(name='Parent graph race fixture')
    original=services.validated
    def changed_graph(*args,**kwargs):
        values=original(*args,**kwargs)
        m.OrgUnit.objects.filter(pk=parent.pk).update(parent=employee.unit)
        return values
    monkeypatch.setattr(services,'validated',changed_graph)
    response=api.patch(f'/api/v1/org-units/{employee.unit_id}',{'parentId':str(parent.pk)})
    assert response.status_code==400 and response.json()['code']=='UNIT_CYCLE'
    employee.unit.refresh_from_db()
    assert employee.unit.parent_id is None

def test_extraction_can_finish_in_trash_and_is_ready_after_restore(hr,employee):
    doc,_,current=create_doc(hr)
    current.extraction_status='PROCESSING';current.save(update_fields=['extraction_status'])
    job,token=running_job(current.file)
    api=hr[2]
    assert api.post(f'/api/v1/content/documents/{doc.id}/trash',{}).status_code==200
    assert result(api,job,token,{'text':'extracted while in trash','pages':1}).status_code==200
    current.refresh_from_db()
    assert current.extraction_status=='READY'
    assert employee[2].get(f'/api/v1/documents/{doc.id}').status_code==404
    assert api.post(f'/api/v1/trash/documents/{doc.id}/restore',{}).status_code==200
    response=employee[2].get(f'/api/v1/documents/{doc.id}/versions/{current.pk}')
    assert response.status_code==200
    assert response.json()['previewText']=='extracted while in trash'


def worker_fetch(api,job,token):
    return api.client.get(f'/api/internal/worker/{job.id}/file',HTTP_AUTHORIZATION='Bearer '+token)


def test_missing_stored_file_ends_scan_in_visible_error_without_retry(hr,settings):
    api=hr[2]
    file=m.FileObject.objects.get(pk=upload(api).json()['id'])
    Path(file.file.path).unlink()  # a host antivirus removed the quarantined bytes
    m.ProcessingJob.objects.filter(file=file).delete()
    job,token=running_job(file,'SCAN')
    file.security_status='SCANNING'
    file.save(update_fields=['security_status'])
    response=worker_fetch(api,job,token)
    assert response.status_code==410 and response.json()=={'code':'FILE_MISSING'}
    job.refresh_from_db(),file.refresh_from_db()
    assert (job.status,job.error_code)==('ERROR','FILE_MISSING') and job.lease_hash==''
    assert (file.security_status,file.rejection_code)==('REJECTED','FILE_MISSING')
    state=api.get(f'/api/v1/files/{file.id}/security-status').json()
    assert state['securityStatus']=='REJECTED' and state['rejectionCode']=='FILE_MISSING'
    claim=api.client.post('/api/internal/worker/claim',{},content_type='application/json',HTTP_AUTHORIZATION='Bearer '+settings.WORKER_TOKEN)
    assert claim.json()=={'job':None}  # the job is terminal, not offered again
    assert worker_fetch(api,job,token).status_code==404  # and the lease is gone


def test_missing_stored_file_marks_extraction_as_error(hr):
    doc,version,_=create_doc(hr)
    Path(version.file.file.path).unlink()
    job,token=running_job(version.file)
    assert worker_fetch(hr[2],job,token).status_code==410
    version.refresh_from_db(),job.refresh_from_db()
    assert version.extraction_status=='ERROR' and version.extraction_error=='FILE_MISSING' and job.status=='ERROR'


def test_extracted_text_with_nul_or_lone_surrogates_is_stored(hr):
    doc,old,_=create_doc(hr)
    job,token=running_job(old.file)
    response=result(hr[2],job,token,{'text':'contract\x00 number \ud800 150','searchText':'extra\x00','pages':1})
    assert response.status_code==200,response.content
    old.refresh_from_db()
    assert old.extraction_status=='READY' and old.extracted_text=='contract number \ufffd 150'
    assert '\x00' not in old.normalized_text


def test_long_request_path_is_audited_and_denied_not_a_server_error(hr,employee):
    doc,old,_=create_doc(hr)
    response=employee[2].post(f'/api/v1/documents/{doc.id}/versions/{old.id}/reprocess',{})
    assert response.status_code==403 and response.json()['code']=='FORBIDDEN'
    assert m.AuditLog.objects.filter(action='ACCESS_DENIED',entity_type='API').exists()


def test_title_that_grows_under_normalization_is_saved(hr):
    response=hr[2].post('/api/v1/documents',{'title':'№'*300,'category':'ORDER'})
    assert response.status_code==201,response.content
    assert len(m.Document.objects.get(pk=response.json()['id']).normalized_title)==300
