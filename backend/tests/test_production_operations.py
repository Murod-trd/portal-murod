"""Operations tests use real encryption/files/TLS; container execution remains a separate gate."""
import io
import json
import os
import subprocess
import sys
import tarfile
from datetime import datetime,timedelta,timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from operations.configuration import ConfigurationError,configure,validate_origin,validate_bind,validate_networks
from operations.crypto_stream import encrypt,decrypt
from operations.volume_archive import pack,unpack
from operations.backup import authenticate,file_digest,signature,prune_backups,restore_backup,key_header

ROOT=Path(__file__).resolve().parents[2]
linux_only=pytest.mark.skipif(sys.platform!='linux',reason='Parser sandbox (seccomp) requires Linux')
posix_modes=pytest.mark.skipif(os.name!='posix',reason='Asserts POSIX 0600 file modes, which Windows does not provide')


@pytest.fixture
def installation_root(tmp_path):
    root=tmp_path/'release';(root/'deploy').mkdir(parents=True)
    for name in ['deploy/images.lock.json','deploy/nginx.conf.in','RELEASE']:(root/name).write_bytes((ROOT/name).read_bytes())
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'portal.test.internal')])
    certificate=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(days=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=30)).add_extension(x509.SubjectAlternativeName([x509.DNSName('portal.test.internal')]),critical=False).sign(key,hashes.SHA256())
    cert=tmp_path/'cert.pem';private=tmp_path/'key.pem'
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM));private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    return root,cert,private


@posix_modes
def test_configuration_creates_independent_private_secrets_and_validates_real_tls(installation_root):
    root,cert,key=installation_root
    result=configure(root,'https://portal.test.internal','10.20.0.10',['10.20.0.0/16','10.8.0.0/24'],cert,key)
    assert result['origin']=='https://portal.test.internal'
    from operations.configuration import read_environment
    values=read_environment(root/'.deploy/portal.env')
    secrets=[values[name] for name in ['DJANGO_SECRET_KEY','WORKER_TOKEN','EDGE_PROXY_TOKEN','POSTGRES_PASSWORD']]
    assert len(set(secrets))==len(secrets) and min(map(len,secrets))>=40
    assert (root/'.deploy/portal.env').stat().st_mode & 0o077==0
    assert len((root/'.deploy/keys/backup.key').read_bytes())==32
    nginx=(root/'.deploy/nginx.conf').read_text()
    assert 'allow 10.8.0.0/24;' in nginx and 'deny all;' in nginx and '@@' not in nginx
    assert 'error_page 413 @upload_too_large;' in nginx and 'default_type application/json;' in nginx  # rendered config keeps the JSON 413
    original=(root/'.deploy/portal.env').read_bytes()
    with pytest.raises(ConfigurationError,match='already exists'):configure(root,'https://portal.test.internal','10.20.0.10',['10.20.0.0/16'],cert,key)
    assert (root/'.deploy/portal.env').read_bytes()==original


@pytest.mark.parametrize('origin',['http://portal.local','https://a@portal.local','https://portal.local/path','https://portal.local?x=1','https://portal.local:99999','https://portal.local\nX'])
def test_invalid_origins_are_rejected(origin):
    with pytest.raises((ConfigurationError,ValueError)):validate_origin(origin)


@pytest.mark.parametrize('address',['0.0.0.0','8.8.8.8','169.254.1.1','::','invalid'])
def test_public_or_unspecified_bind_is_rejected(address):
    with pytest.raises(ConfigurationError):validate_bind(address)


def test_unrestricted_network_and_wrong_cert_are_rejected(installation_root):
    with pytest.raises(ConfigurationError):validate_networks(['0.0.0.0/0'])
    root,cert,key=installation_root
    with pytest.raises(ConfigurationError):configure(root,'https://wrong.test.internal','127.0.0.1',['127.0.0.1/32'],cert,key)
    assert not (root/'.deploy/portal.env').exists()


@pytest.mark.parametrize('data',[b'',b'private synthetic data',b'x'*(2*1024*1024+13)])
def test_authenticated_encryption_round_trip_and_no_plaintext(data):
    key=os.urandom(32);encrypted=io.BytesIO();encrypt(io.BytesIO(data),encrypted,key)
    first=encrypted.getvalue();second=io.BytesIO();encrypt(io.BytesIO(data),second,key)
    assert first!=second.getvalue()
    output=io.BytesIO();decrypt(io.BytesIO(first),output,key)
    assert output.getvalue()==data
    decrypt(io.BytesIO(first),None,key)


@pytest.mark.parametrize('damage',['body','header','tag','truncate','wrong-key'])
def test_backup_corruption_fails_authentication(damage):
    key=os.urandom(32);encrypted=io.BytesIO();encrypt(io.BytesIO(b'synthetic secret document'*100),encrypted,key);data=bytearray(encrypted.getvalue())
    if damage=='body':data[40]^=1
    if damage=='header':data[6]^=1
    if damage=='tag':data[-1]^=1
    if damage=='truncate':data=data[:-12]
    if damage=='wrong-key':key=os.urandom(32)
    with pytest.raises(Exception):decrypt(io.BytesIO(data),None,key)


def test_real_codec_subprocess_uses_stdin_key_and_authenticates():
    key=os.urandom(32);plain=b'actual encrypted subprocess fixture'
    encrypted=subprocess.run([sys.executable,str(ROOT/'operations/crypto_stream.py'),'encrypt'],input=key_header(key)+plain,capture_output=True,check=True).stdout
    decrypted=subprocess.run([sys.executable,str(ROOT/'operations/crypto_stream.py'),'decrypt'],input=key_header(key)+encrypted,capture_output=True,check=True).stdout
    assert decrypted==plain and key not in encrypted
    invalid=subprocess.run([sys.executable,str(ROOT/'operations/crypto_stream.py'),'verify'],input=key_header(os.urandom(32))+encrypted,capture_output=True,check=False)
    assert invalid.returncode!=0 and invalid.stdout==b'' and key not in invalid.stderr


@posix_modes
def test_private_volume_round_trip_preserves_old_version_bytes(tmp_path):
    source=tmp_path/'source';(source/'quarantine/2026').mkdir(parents=True)
    (source/'quarantine/2026/old.upload').write_bytes(b'old immutable document bytes')
    (source/'quarantine/2026/new.upload').write_bytes(b'new immutable document bytes')
    raw=io.BytesIO();pack(source,raw)
    restored=tmp_path/'restored';unpack(io.BytesIO(raw.getvalue()),restored)
    assert (restored/'quarantine/2026/old.upload').read_bytes()==b'old immutable document bytes'
    assert (restored/'quarantine/2026/new.upload').read_bytes()==b'new immutable document bytes'
    assert (restored/'quarantine/2026/old.upload').stat().st_mode & 0o077==0
    with pytest.raises(ValueError,match='empty'):unpack(io.BytesIO(raw.getvalue()),restored)


@pytest.mark.parametrize('kind',['traversal','absolute','symlink','hardlink','duplicate'])
def test_restore_rejects_unsafe_archive_members(tmp_path,kind):
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w') as archive:
        item=tarfile.TarInfo('../escape' if kind=='traversal' else '/absolute' if kind=='absolute' else 'item')
        if kind in {'symlink','hardlink'}:item.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE;item.linkname='../escape'
        else:item.size=1
        archive.addfile(item,io.BytesIO(b'x') if item.isfile() else None)
        if kind=='duplicate':archive.addfile(item,io.BytesIO(b'y'))
    with pytest.raises(ValueError):unpack(io.BytesIO(raw.getvalue()),tmp_path/'restore')
    assert not (tmp_path/'escape').exists()


def write_snapshot(root,key,age=0,name='20260101T000000Z-aabbcc'):
    path=root/name;path.mkdir()
    for item in ['database.enc','files.enc']:
        with (path/item).open('wb') as destination:encrypt(io.BytesIO(b'synthetic snapshot'),destination,key)
    value={'format':1,'release':'test-release','createdAt':(datetime.now(timezone.utc)-timedelta(days=age)).isoformat(),'files':{item:{'bytes':(path/item).stat().st_size,'sha256':file_digest(path/item)} for item in ['database.enc','files.enc']}}
    value['authentication']=signature(value,key);(path/'manifest.json').write_text(json.dumps(value))
    return path


def test_manifest_tampering_prevents_any_restore_side_effect(tmp_path):
    key=os.urandom(32);path=write_snapshot(tmp_path,key)
    assert authenticate(path,key)['release']=='test-release'
    value=json.loads((path/'manifest.json').read_text());value['release']='attacker-change';(path/'manifest.json').write_text(json.dumps(value))
    class FakeController:
        installation={'hostname':'portal.test.internal'}
        environment={}
        def backup_key(self):return key
        def run(self,*args,**kwargs):raise AssertionError('No service must be touched')
    with pytest.raises(ConfigurationError,match='authentication'):restore_backup(FakeController(),path,'portal.test.internal',True)


def test_retention_preserves_recent_minimum_and_unrelated_directories(tmp_path):
    key=os.urandom(32)
    for index in range(5):write_snapshot(tmp_path,key,age=20+index,name=f'2026010{index+1}T000000Z-aabbcc')
    unrelated=tmp_path/'customer-documents';unrelated.mkdir();(unrelated/'keep.txt').write_text('keep')
    assert prune_backups(tmp_path,key,days=14,keep=3)==2
    assert len(list(tmp_path.glob('2026*')))==3 and (unrelated/'keep.txt').exists()


def test_dockerfile_runtime_has_no_node_and_build_args_are_global():
    dockerfile=(ROOT/'Dockerfile').read_text();prefix=dockerfile.split('FROM ',1)[0]
    assert 'ARG PYTHON_IMAGE=' in prefix and 'ARG NODE_IMAGE=' in prefix
    runtime=dockerfile.split('FROM ${PYTHON_IMAGE} AS api',1)[1]
    assert 'COPY --from=frontend /build/frontend-dist' in runtime
    assert 'COPY --from=frontend /usr/local/bin/node' not in runtime

def test_origin_is_canonical_for_browser_csrf():
    assert validate_origin('https://PORTAL.Example:443/')[0]=='https://portal.example'


def test_systemd_units_use_fixed_operations_and_preserve_unrelated_units(tmp_path):
    from operations.service_units import write_units
    directory=tmp_path/'units'
    names=write_units(ROOT,directory)
    assert len(names)==4
    assert 'backup' in (directory/'toshkent-portal-backup.service').read_text()
    (directory/'toshkent-portal-backup.service').write_text('Unrelated service')
    with pytest.raises(ConfigurationError):write_units(ROOT,directory)
    assert (directory/'toshkent-portal-backup.service').read_text()=='Unrelated service'


def test_operator_environment_cannot_override_generated_configuration(installation_root,monkeypatch):
    from operations.control import Controller
    root,cert,key=installation_root
    configure(root,'https://portal.test.internal','10.20.0.10',['10.20.0.0/16'],cert,key)
    monkeypatch.setenv('APP_ORIGIN','https://wrong.invalid');monkeypatch.setenv('COMPOSE_FILE','unrelated-project.yml')
    controller=Controller(root)
    assert 'APP_ORIGIN' not in controller.process_environment and 'COMPOSE_FILE' not in controller.process_environment
    assert controller.environment['APP_ORIGIN']=='https://portal.test.internal'

@linux_only
def test_real_parser_seccomp_blocks_network_and_parent_signals():
    script='''
import errno,json,os,socket,subprocess
from portal.processor_sandbox import restrict_parser
restrict_parser()
result={}
try: socket.socket(); result['networkBlocked']=False
except OSError as error: result['networkBlocked']=error.errno==errno.EPERM
try: os.kill(os.getppid(),0); result['parentSignalsBlocked']=False
except OSError as error: result['parentSignalsBlocked']=error.errno==errno.EPERM
result['ordinaryChildWorks']=subprocess.run(['/bin/true'],check=False).returncode==0
print(json.dumps(result))
'''
    env={**os.environ,'PYTHONPATH':str(ROOT/'backend')}
    value=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=15,check=True)
    assert json.loads(value.stdout)=={'networkBlocked':True,'parentSignalsBlocked':True,'ordinaryChildWorks':True}


@linux_only
def test_parser_refuses_work_when_seccomp_library_is_missing():
    script='''
import ctypes,ctypes.util
from portal.processor_sandbox import restrict_parser
original=ctypes.CDLL
ctypes.util.find_library=lambda name: None
def missing(name,*args,**kwargs):
    if name is None:return original(name,*args,**kwargs)
    raise OSError('Synthetic missing library')
ctypes.CDLL=missing
try:restrict_parser()
except RuntimeError as error:
    assert str(error)=='libseccomp is required'
else:raise AssertionError('Parser continued without seccomp')
'''
    env={**os.environ,'PYTHONPATH':str(ROOT/'backend')}
    subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=15,check=True)

@pytest.mark.parametrize('failure',['migration','pipeline','backup','none'])
def test_install_never_reports_success_without_migrations_pipeline_and_backup(tmp_path,monkeypatch,failure):
    from types import SimpleNamespace
    from operations.control import install
    calls=[]
    class FakeController:
        directory=tmp_path
        environment={'PORTAL_RELEASE':'test-release'}
        def run(self,*args,**kwargs):
            calls.append(args)
            if failure=='migration' and args[0]=='run' and args[-1]=='migrate':raise ConfigurationError('migration failure')
        def ensure_search_extension(self):calls.append(('extension',))
        def wait_ready(self):calls.append(('ready',))
        def runtime_check(self,**kwargs):
            calls.append(('check',kwargs))
            if failure=='pipeline':raise ConfigurationError('pipeline failure')
            return {'checks':{'activeAdministrator':True,'backupFresh':True}}
        def backup_directory(self):return tmp_path/'backups'
    monkeypatch.setattr('operations.control.preflight',lambda controller:None)
    def backup(*args,**kwargs):
        calls.append(('backup',))
        if failure=='backup':raise OSError('backup destination unavailable')
        return tmp_path/'snapshot'
    monkeypatch.setattr('operations.backup.make_backup',backup)
    args=SimpleNamespace(admin_login='individual.admin',admin_name='Individual Administrator',password_stdin=True)
    if failure=='none':
        assert install(FakeController(),args)['initialBackup']==str(tmp_path/'snapshot')
        assert (tmp_path/'deployment-complete.json').exists()
        assert calls.index(('backup',))<len(calls)-1
    else:
        with pytest.raises((ConfigurationError,OSError)):install(FakeController(),args)
        assert not (tmp_path/'deployment-complete.json').exists()
        assert calls[-1]==('stop','proxy')
        if failure=='migration':assert not any(call[0]=='up' and 'app' in call for call in calls)


def test_reinstall_guard_does_not_touch_an_existing_live_portal(tmp_path,monkeypatch):
    from operations.control import install
    (tmp_path/'deployment-complete.json').write_text('{}')
    class FakeController:
        directory=tmp_path
        def run(self,*args,**kwargs):raise AssertionError('A live portal must not be touched')
    monkeypatch.setattr('operations.control.preflight',lambda controller:pytest.fail('Reinstall must stop before preflight'))
    with pytest.raises(ConfigurationError,match='already active'):install(FakeController(),None)


def test_restore_can_authenticate_external_recovery_key_without_overwriting_current_key(tmp_path):
    recovery=os.urandom(32);current=os.urandom(32)
    snapshot=write_snapshot(tmp_path,recovery);key_file=tmp_path/'external-recovery.key';key_file.write_bytes(recovery)
    class FakeController:
        installation={'hostname':'portal.test.internal'}
        environment={}
        process_environment=os.environ.copy()
        def backup_key(self):return current
        def image_available(self,release):return True
        def codec(self,mode):return [sys.executable,str(ROOT/'operations/crypto_stream.py'),mode]
        def run(self,*args,**kwargs):raise AssertionError('No destructive action without explicit replacement confirmation')
    with pytest.raises(ConfigurationError,match='explicitly add --replace'):
        restore_backup(FakeController(),snapshot,'portal.test.internal',False,key_file)
    assert key_file.read_bytes()==recovery

def test_backup_containers_never_write_plaintext_streams_into_docker_logs():
    import yaml
    compose=yaml.safe_load((ROOT/'compose.yaml').read_text())
    for service in ['backup-tool','restore-tool']:
        assert compose['services'][service]['logging']['driver']=='none'
        assert compose['services'][service]['network_mode']=='none'
    assert 'private-files:/var/lib/portal/files' not in compose['services']['worker'].get('volumes',[])


def test_every_runtime_base_image_is_fixed_by_registry_digest():
    images=json.loads((ROOT/'deploy/images.lock.json').read_text())['images']
    assert set(images)=={'postgres','python','node','nginx','clamav'}
    for reference in images.values():assert '@sha256:' in reference and len(reference.rsplit(':',1)[1])==64

def test_tmpfs_options_are_single_absolute_mount_entries():
    import yaml
    compose=yaml.safe_load((ROOT/'compose.yaml').read_text())
    for service in compose['services'].values():
        for mount in service.get('tmpfs',[]):
            assert isinstance(mount,str) and mount.startswith('/'),mount
    worker=compose['services']['worker']['tmpfs']
    assert any(mount.startswith('/run/portal-worker:') and 'uid=10001' in mount for mount in worker)

def test_oversized_backup_manifest_is_rejected_before_parsing(tmp_path):
    directory=tmp_path/'snapshot';directory.mkdir();(directory/'manifest.json').write_bytes(b'x'*65537)
    with pytest.raises(ConfigurationError,match='manifest file'):authenticate(directory,os.urandom(32))

def test_installer_refuses_unrelated_existing_volumes_before_mutating_services(monkeypatch):
    from operations.control import check_project_ownership
    from types import SimpleNamespace
    class FakeController:
        installation={'installationId':'this-installation'}
        environment={'COMPOSE_PROJECT_NAME':'toshkent-invest-portal'}
        process_environment={}
        def run(self,*args,**kwargs):return b''
    monkeypatch.setattr('operations.control.subprocess.run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps({'org.toshkent.portal.installation':'another-installation'}).encode()))
    with pytest.raises(ConfigurationError,match='different installation'):check_project_ownership(FakeController())


@pytest.mark.parametrize('failure',['readiness','technical-check','post-restore-backup'])
def test_restore_closes_access_if_resumed_services_fail_acceptance(tmp_path,monkeypatch,failure):
    key=os.urandom(32);snapshot=write_snapshot(tmp_path,key);calls=[]
    class FakeController:
        installation={'hostname':'portal.test.internal'}
        environment={}
        process_environment={}
        directory=tmp_path
        def backup_key(self):return key
        def image_available(self,release):return True
        def codec(self,mode):return ['codec',mode]
        def command(self,*args):return list(args)
        def run(self,*args,**kwargs):calls.append(args)
        def backup_directory(self):return tmp_path/'backups'
        def use_release(self,release):pass
        def grant_runtime_permissions(self):pass
        def recover(self,**kwargs):pass
        def wait_ready(self):
            if failure=='readiness':raise ConfigurationError('worker unavailable')
        def runtime_check(self,**kwargs):
            if failure=='technical-check':raise ConfigurationError('file integrity failed')
    backup_calls=[]
    def backup(*args,**kwargs):
        backup_calls.append(True)
        if len(backup_calls)==2 and failure=='post-restore-backup':raise OSError('backup disk full')
        return tmp_path/'safety-snapshot'
    monkeypatch.setattr('operations.backup.make_backup',backup)
    monkeypatch.setattr('operations.backup.consume_encrypted',lambda *args,**kwargs:None)
    with pytest.raises(ConfigurationError,match='Post-restore verification failed'):
        restore_backup(FakeController(),snapshot,'portal.test.internal',True)
    assert calls[-1]==('stop','proxy','worker','app')


def test_configuration_allows_isolated_recovery_project_and_rejects_command_text(installation_root):
    root, cert, key = installation_root
    for invalid in ['../other', 'UPPERCASE', 'a\nPASSWORD=bad', 'a;command']:
        with pytest.raises(ConfigurationError, match='project name'):
            configure(root, 'https://portal.test.internal', '127.0.0.1', ['127.0.0.1/32'], cert, key, project_name=invalid)
    configure(root, 'https://portal.test.internal', '127.0.0.1', ['127.0.0.1/32'], cert, key, project_name='portal-recovery-acceptance')
    from operations.configuration import read_environment
    assert read_environment(root/'.deploy/portal.env')['COMPOSE_PROJECT_NAME'] == 'portal-recovery-acceptance'


def test_nginx_answers_oversize_uploads_with_the_api_error_shape():
    import json,re
    template=(ROOT/'deploy/nginx.conf.in').read_text()
    assert re.search(r'client_max_body_size 21m;',template)  # 20 MB file plus multipart overhead
    named=re.search(r'location @upload_too_large \{(.*?)\n    \}',template,re.S).group(1)
    assert 'default_type application/json;' in named and 'X-Content-Type-Options "nosniff" always;' in named and 'Cache-Control "no-store" always;' in named
    body=re.search(r"return 413 '(.*)';",named).group(1)
    assert json.loads(body)=={'code':'UPLOAD_SIZE_LIMIT'}
    server=template[template.index('server {'):]
    assert server.index('error_page 413 @upload_too_large;')<server.index('location ^~ /api/internal/')  # declared once for the whole server
    assert template.count('error_page 413')==1 and 'proxy_intercept_errors' not in template  # application answers are never rewritten
