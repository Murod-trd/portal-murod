"""Runtime guards are exercised with simulated AV health, not a real ClamAV engine."""
import io
import json
import os
from datetime import timedelta
import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone
from portal import models as m
from portal.domain import search_fold
from .test_content_and_files import approved_fixture,create_doc

pytestmark=pytest.mark.django_db


def test_production_requires_authenticated_proxy_and_rejects_spoofed_forwarding(settings):
    settings.PRODUCTION=True;settings.EDGE_PROXY_TOKEN='test-edge-key-'+('x'*48)
    client=Client()
    assert client.get('/api/v1/auth/csrf').status_code==404
    assert client.get('/api/v1/auth/csrf',HTTP_X_PORTAL_PROXY_KEY=settings.EDGE_PROXY_TOKEN,HTTP_X_FORWARDED_FOR='10.1.0.1, attacker').status_code==400
    assert client.get('/api/v1/auth/csrf',HTTP_X_PORTAL_PROXY_KEY=settings.EDGE_PROXY_TOKEN,HTTP_X_FORWARDED_FOR='10.1.0.1').status_code==200


def test_only_worker_token_can_report_health(hr):
    client=hr[2].client
    assert client.post('/api/internal/worker/heartbeat',{'ocrLanguages':['eng']},content_type='application/json').status_code==401
    good=client.post('/api/internal/worker/heartbeat',{'ocrLanguages':['eng','uzb']},content_type='application/json',HTTP_AUTHORIZATION='Bearer '+settings.WORKER_TOKEN)
    assert good.status_code==200
    assert m.WorkerHeartbeat.objects.get(name='file-worker').capabilities=={'ocrLanguages':['eng','uzb'],'processorSandbox':False}


@pytest.mark.skipif(os.name!='posix',reason='Readiness checks POSIX private-directory modes, which Windows does not provide')
def test_readiness_requires_live_worker_models_and_private_storage(settings,tmp_path,monkeypatch):
    settings.PRODUCTION=True;settings.STORAGE_DRIVER='filesystem'
    settings.MEDIA_ROOT.mkdir(mode=0o700,parents=True)
    settings.BASE_DIR=tmp_path/'application';(settings.BASE_DIR/'frontend-dist').mkdir(parents=True);(settings.BASE_DIR/'frontend-dist/index.html').write_text('frontend')
    monkeypatch.setattr('portal.file_processing.clamav_scan',lambda *args,**kwargs:{'scannerVersion':'PROTOCOL-STUB','signatureVersion':'NOT-REAL-AV'})
    client=Client()
    assert client.get('/api/health/ready').status_code==503
    worker=m.WorkerHeartbeat.objects.create(name='file-worker',seen_at=timezone.now(),capabilities={'ocrLanguages':settings.OCR_LANGUAGES.split('+'),'processorSandbox':True})
    assert client.get('/api/health/ready').json()=={'status':'ready'}
    worker.seen_at=timezone.now()-timedelta(minutes=2);worker.save()
    assert client.get('/api/health/ready').status_code==503
    worker.seen_at=timezone.now();worker.capabilities={'ocrLanguages':['eng']};worker.save()
    assert client.get('/api/health/ready').status_code==503
    worker.capabilities={'ocrLanguages':settings.OCR_LANGUAGES.split('+'),'processorSandbox':True};worker.save()
    settings.MEDIA_ROOT.chmod(0o755)
    assert client.get('/api/health/ready').status_code==503


def test_maintenance_requeues_work_without_approving_files_and_restore_invalidates_sessions(hr):
    file=approved_fixture(hr[0]);file.security_status='SCANNING';file.save()
    job=m.ProcessingJob.objects.create(file=file,kind='SCAN',status='RUNNING',attempts=1,lease_hash='old-lease',lease_expires=timezone.now()+timedelta(minutes=10))
    with pytest.raises(CommandError):call_command('recover_processing',stdout=io.StringIO())
    version=hr[0].portal_account.session_version
    call_command('recover_processing',workers_stopped=True,invalidate_sessions=True,stdout=io.StringIO())
    job.refresh_from_db();file.refresh_from_db();hr[0].portal_account.refresh_from_db()
    assert job.status=='PENDING' and job.attempts==0 and not job.lease_hash
    assert file.security_status=='QUARANTINED'
    assert hr[0].portal_account.session_version==version+1
    assert hr[2].get('/api/v1/me').status_code==401


def test_restore_checks_actual_file_bytes_and_reports_no_personal_filenames(hr):
    file=approved_fixture(hr[0],name='sensitive-name-must-not-be-reported.pdf')
    output=io.StringIO();call_command('verify_private_files',stdout=output)
    assert json.loads(output.getvalue())=={'checked':1,'missing':0,'changed':0}
    file.file.storage.delete(file.file.name)
    output=io.StringIO()
    with pytest.raises(CommandError):call_command('verify_private_files',stdout=output)
    assert json.loads(output.getvalue())['missing']==1
    assert file.original_name not in output.getvalue()


@pytest.mark.parametrize('typed,ocr',[
    ('ĞALABA VA ŞAHAR','GALABA VA SAHAR'),('ÖZBEKÇA','OZBEKCA'),('ЎЗБЕКЧА ҲУЖЖАТ','УЗБЕКЧА ХУЖЖАТ'),('ҚАРОР ҒАЛАБА','КАРОР ГАЛАБА'),("G‘alaba shahar",'GALABA SAHAR'),
])
def test_document_content_search_tolerates_missing_uzbek_diacritics(hr,employee,typed,ocr):
    doc,_,_=create_doc(hr,new_text=ocr)
    result=employee[2].get('/api/v1/documents',{'q':typed,'inside':'true'}).json()
    assert result['total']==1 and result['items'][0]['id']==str(doc.pk)
    assert employee[2].get('/api/v1/documents',{'q':typed}).json()['total']==0
    assert search_fold(typed)==search_fold(ocr)


def test_live_ocr_probe_pdf_can_be_read_by_the_real_worker(monkeypatch):
    import shutil
    if not shutil.which('tesseract') or not shutil.which('pdftoppm'):pytest.skip('Native OCR tools are unavailable')
    from portal.management.commands.verify_deployment import make_probe_pdf
    from worker import execute
    monkeypatch.setenv('OCR_LANGUAGES','eng');monkeypatch.delenv('TESSDATA_PREFIX',raising=False)
    value=execute({'kind':'EXTRACT','fileName':'installation-probe.pdf','purpose':'DOCUMENT'},make_probe_pdf())
    assert 'PIPELINE CHECK NUMBER 150' in value.get('text','').upper()

def test_non_ascii_auth_headers_are_rejected_without_server_error(settings):
    settings.PRODUCTION=True;settings.EDGE_PROXY_TOKEN='test-edge-'+('x'*48)
    client=Client()
    assert client.get('/api/v1/auth/csrf',HTTP_X_PORTAL_PROXY_KEY='é-invalid',HTTP_X_FORWARDED_FOR='10.1.1.1').status_code==404
    assert client.post('/api/internal/worker/heartbeat',{},content_type='application/json',HTTP_AUTHORIZATION='Bearer é-invalid').status_code==401
