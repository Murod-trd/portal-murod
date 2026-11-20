"""Acceptance safety and result integrity without pretending Docker was executed."""
import hashlib
import io
import json
from types import SimpleNamespace
import pytest
from operations.acceptance import Acceptance,StageFailed,copy_release,parse_marker
from operations.configuration import ConfigurationError,configure
from operations.native_checks import permitted
from .test_production_operations import installation_root as installation_root


def release(root):
    root.mkdir();(root/'README.md').write_text('Synthetic release')
    (root/'frontend-dist').mkdir();(root/'frontend-dist/index.html').write_text('<p>Synthetic prebuilt UI</p>')
    (root/'frontend-dist/BUILD-PROVENANCE.json').write_text('{}')
    names=['README.md','frontend-dist/index.html','frontend-dist/BUILD-PROVENANCE.json']
    value={'format':1,'release':'test-release','sourceCommit':'synthetic','sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}}
    (root/'RELEASE-MANIFEST.json').write_text(json.dumps(value))
    return root


def test_acceptance_without_docker_is_blocked_before_copy_or_child_process(tmp_path,monkeypatch):
    source=release(tmp_path/'release');run=Acceptance(tmp_path/'lab',source)
    monkeypatch.setattr('operations.acceptance.host_blockers',lambda:['docker is unavailable'])
    monkeypatch.setattr(run,'execute',lambda *args,**kwargs:pytest.fail('No subprocess before usable host'))
    assert run.run()==2
    result=json.loads((tmp_path/'lab/evidence/report.json').read_text())
    assert not result['technicalChecksPassed'] and not result['productionApproved']
    assert result['stages']==[{'name':'host','status':'blocked'}]
    assert not (tmp_path/'lab/source').exists()


def test_acceptance_never_overwrites_existing_workspace(tmp_path):
    source=release(tmp_path/'release');lab=tmp_path/'lab';lab.mkdir();(lab/'keep').write_bytes(b'existing data')
    with pytest.raises(FileExistsError):Acceptance(lab,source)
    assert (lab/'keep').read_bytes()==b'existing data'


def test_acceptance_checks_docker_context_before_registering_cleanup_or_starting_resources(tmp_path,monkeypatch):
    source=release(tmp_path/'release');run=Acceptance(tmp_path/'lab',source);calls=[]
    monkeypatch.setattr(run,'execute',lambda name,*args,**kwargs:calls.append(name))
    monkeypatch.setattr('operations.acceptance.configure',lambda *args,**kwargs:None)
    monkeypatch.setattr('operations.acceptance.Controller',lambda checkout:SimpleNamespace(root=checkout))
    def reject(controller):raise ConfigurationError('Remote Docker context is forbidden')
    monkeypatch.setattr('operations.acceptance.preflight',reject)
    with pytest.raises(ConfigurationError,match='Remote Docker'):run.prepare()
    assert calls==['test-tls-validity'] and run.controllers==[]


def test_release_copy_requires_integrity_and_is_separate_from_source(tmp_path):
    source=release(tmp_path/'release')
    with pytest.raises(ConfigurationError):copy_release(source,source/'nested')
    (source/'README.md').write_text('Changed content')
    with pytest.raises(ConfigurationError,match='changed'):copy_release(source,tmp_path/'copy')
    assert not (tmp_path/'copy').exists()


def test_release_without_prebuilt_frontend_is_refused(tmp_path):
    source=release(tmp_path/'release');manifest=json.loads((source/'RELEASE-MANIFEST.json').read_text())
    del manifest['sha256']['frontend-dist/index.html'];(source/'RELEASE-MANIFEST.json').write_text(json.dumps(manifest))
    with pytest.raises(ConfigurationError,match='prebuilt frontend'):copy_release(source,tmp_path/'copy')
    assert not (tmp_path/'copy').exists()


def test_release_copy_omits_unlisted_secret_files(tmp_path):
    source=release(tmp_path/'release');(source/'private.env').write_text('SECRET=not-to-be-copied')
    copy_release(source,tmp_path/'copy')
    assert (tmp_path/'copy/README.md').read_text()=='Synthetic release'
    assert not (tmp_path/'copy/private.env').exists()


def test_failure_report_never_promotes_partial_run_to_success(tmp_path,monkeypatch):
    source=release(tmp_path/'release');run=Acceptance(tmp_path/'lab',source)
    monkeypatch.setattr('operations.acceptance.host_blockers',lambda:[])
    monkeypatch.setattr(run,'prepare',lambda:True)
    monkeypatch.setattr(run,'native',lambda:(_ for _ in ()).throw(StageFailed('Native test failed')))
    monkeypatch.setattr(run,'install',lambda index:None)
    monkeypatch.setattr(run,'scan',lambda scope:True);monkeypatch.setattr(run,'cleanup',lambda:None)
    assert run.run()==1 and not run.report['technicalChecksPassed']
    assert any(s['name']=='native-postgres-tests-and-benchmark' and s['status']=='failed'for s in run.report['stages'])
    assert not any(s['name']=='independent-volume-restoration'for s in run.report['stages'])


def test_native_checks_refuse_normal_database_settings():
    base={'APP_ENV':'test','DB_ENGINE':'postgres','POSTGRES_HOST':'postgres','POSTGRES_DB':'portal_acceptance','PORTAL_ACCEPTANCE_RUN_ID':'a'*24}
    assert permitted(base)
    for key,value in [('APP_ENV','production'),('DB_ENGINE','sqlite'),('POSTGRES_HOST','company-db'),('POSTGRES_DB','portal'),('PORTAL_ACCEPTANCE_RUN_ID','')]:assert not permitted({**base,key:value})


def test_native_report_requires_real_structured_marker(tmp_path):
    log=tmp_path/'native.log';log.write_text('194 passed\n')
    with pytest.raises(StageFailed):parse_marker(log,'PORTAL_NATIVE_CHECKS=')
    log.write_text('log\nPORTAL_NATIVE_CHECKS={"tests":{"passed":false}}\n')
    assert not parse_marker(log,'PORTAL_NATIVE_CHECKS=')['tests']['passed']


def test_disposable_image_tags_are_isolated_from_production(installation_root,monkeypatch):
    from operations.control import Controller
    root,cert,key=installation_root
    configure(root,'https://portal.test.internal','127.0.0.1',['127.0.0.1/32'],cert,key,project_name='private-acceptance')
    controller=Controller(root);calls=[]
    def inspect(arguments,**kwargs):calls.append(arguments);return SimpleNamespace(returncode=0)
    monkeypatch.setattr('operations.control.subprocess.run',inspect)
    assert controller.image_available('test-release')
    assert {a[-1]for a in calls}=={'private-acceptance-api:test-release','private-acceptance-worker:test-release'}


def test_acceptance_checks_ownership_before_removing_lab(tmp_path,monkeypatch):
    source=release(tmp_path/'release');run=Acceptance(tmp_path/'lab',source)
    run.controllers=[SimpleNamespace(directory=tmp_path/'lab/private/locks')]
    monkeypatch.setattr('operations.acceptance.check_project_ownership',lambda c:(_ for _ in ()).throw(ConfigurationError('Foreign volume')))
    monkeypatch.setattr(run,'execute',lambda *args,**kwargs:pytest.fail('No removal of foreign resources'))
    run.cleanup()
    assert run.report['stages']==[{'name':'cleanup-0','status':'failed','errorType':'ConfigurationError'}]


def test_technical_success_never_certifies_external_acceptance(tmp_path,monkeypatch):
    source=release(tmp_path/'release');run=Acceptance(tmp_path/'lab',source)
    monkeypatch.setattr('operations.acceptance.host_blockers',lambda:[])
    for name in ['prepare','native','restore','http_workflows']:monkeypatch.setattr(run,name,lambda:True)
    monkeypatch.setattr(run,'install',lambda index:True);monkeypatch.setattr(run,'scan',lambda scope:True);monkeypatch.setattr(run,'cleanup',lambda:None)
    assert run.run()==0 and run.report['technicalChecksPassed'] and not run.report['productionApproved']
    assert run.report['externalGates']['independentPentest']=='NOT_PERFORMED'


@pytest.mark.django_db
def test_restore_inventory_compares_auth_and_mfa_without_exposing_secrets(admin):
    from django.core.management import call_command
    from django.db.models import F
    from portal.models import UserAccount
    def inventory():
        output=io.StringIO();call_command('recovery_inventory',stdout=output);return json.loads(output.getvalue()),output.getvalue()
    before,text=inventory();device=admin[0].totpdevice_set.get()
    assert before['User']['rows'] and before['TOTPDevice']['rows']
    assert admin[0].username not in text and device.key not in text and admin[0].password not in text
    UserAccount.objects.filter(pk=admin[0].pk).update(session_version=F('session_version')+1)
    after,_=inventory();assert before['UserAccount']==after['UserAccount']
    device.key='a'*40;device.save(update_fields=['key'])
    changed,_=inventory();assert changed['TOTPDevice']['sha256']!=before['TOTPDevice']['sha256']
