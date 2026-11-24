"""Host-side, stdlib-only orchestration. Keys stay on stdin; data stays encrypted on disk."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import threading
from datetime import datetime,timezone
from pathlib import Path
from .configuration import ConfigurationError,private_write


def file_digest(path):
    digest=hashlib.sha256()
    with open(path,'rb') as source:
        while block:=source.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def signature(manifest,key):
    derived=hmac.new(key,b'Toshkent Invest backup manifest v1',hashlib.sha256).digest()
    return hmac.new(derived,json.dumps(manifest,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()


def authenticate(directory,key):
    directory=Path(directory).resolve()
    descriptor=directory/'manifest.json'
    if descriptor.is_symlink() or descriptor.stat().st_size>65536:raise ConfigurationError('Invalid backup manifest file')
    manifest=json.loads(descriptor.read_text())
    if not isinstance(manifest,dict):raise ConfigurationError('Invalid backup manifest')
    supplied=manifest.pop('authentication','')
    if not isinstance(supplied,str) or len(supplied)!=64 or any(character not in '0123456789abcdef' for character in supplied):raise ConfigurationError('Invalid backup authentication')
    if manifest.get('format')!=1 or not hmac.compare_digest(supplied,signature(manifest,key)):
        raise ConfigurationError('Backup manifest authentication failed')
    if set(manifest.get('files',{}))!={'database.enc','files.enc'}:raise ConfigurationError('Invalid backup members')
    for name,metadata in manifest['files'].items():
        path=directory/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=metadata['bytes'] or file_digest(path)!=metadata['sha256']:
            raise ConfigurationError('Backup data is missing or has changed')
    return manifest


def key_header(key):
    if len(key)!=32:raise ConfigurationError('Backup key must be 32 bytes')
    return json.dumps({'key':base64.b64encode(key).decode()}).encode()+b'\n'


def encrypt_command(source_command,codec_command,destination,key,environment=None):
    with open(destination,'xb') as output:
        os.chmod(destination,0o600)
        # Errors intentionally omit command/environment dumps and plaintext contents.
        source=subprocess.Popen(source_command,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        codec=subprocess.Popen(codec_command,env=environment,stdin=subprocess.PIPE,stdout=output,stderr=subprocess.DEVNULL)
        watchdog=threading.Timer(3600,lambda:[process.kill() for process in [source,codec] if process.poll() is None]);watchdog.daemon=True;watchdog.start()
        try:
            codec.stdin.write(key_header(key));shutil.copyfileobj(source.stdout,codec.stdin,1024*1024)
            codec.stdin.close();source.stdout.close()
            first=source.wait(timeout=3600);second=codec.wait(timeout=3600)
            output.flush();os.fsync(output.fileno())
            if first or second:raise ConfigurationError('Encrypted backup stream failed')
        except BaseException:
            for process in [source,codec]:
                if process.poll() is None:process.kill()
            for process in [source,codec]:process.wait()
            raise
        finally:watchdog.cancel()


def consume_encrypted(codec_command,path,key,destination_command=None,environment=None):
    consumer=subprocess.Popen(destination_command,env=environment,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) if destination_command else None
    codec=subprocess.Popen(codec_command,env=environment,stdin=subprocess.PIPE,stdout=consumer.stdin if consumer else subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if consumer:consumer.stdin.close()
    watchdog=threading.Timer(3600,lambda:[process.kill() for process in [codec,consumer] if process is not None and process.poll() is None]);watchdog.daemon=True;watchdog.start()
    try:
        codec.stdin.write(key_header(key))
        with open(path,'rb') as encrypted:shutil.copyfileobj(encrypted,codec.stdin,1024*1024)
        codec.stdin.close()
        first=codec.wait(timeout=3600);second=consumer.wait(timeout=3600) if consumer else 0
        if first or second:raise ConfigurationError('Backup authentication/restore stream failed')
    except BaseException:
        for process in [codec,consumer]:
            if process is not None and process.poll() is None:process.kill()
        for process in [codec,consumer]:
            if process is not None:process.wait()
        raise
    finally:watchdog.cancel()


def make_backup(controller,directory,*,resume_services=True):
    key=controller.backup_key();parent=Path(directory).resolve();parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)
    target=parent/stamp;temporary=parent/('.partial-'+stamp);temporary.mkdir(mode=0o700)
    paused=False
    try:
        paused=True;controller.run('stop','proxy','worker','app')
        controller.recover()
        encrypt_command(controller.command('exec','-T','postgres','pg_dump','-U','postgres','-Fc','--no-owner','--exclude-extension=pg_trgm','portal'),controller.codec('encrypt'),temporary/'database.enc',key,controller.process_environment)
        encrypt_command(controller.command('run','--rm','-T','--no-deps','backup-tool','operations/volume_archive.py','pack'),controller.codec('encrypt'),temporary/'files.enc',key,controller.process_environment)
        manifest={'format':1,'keyId':hashlib.sha256(key).hexdigest()[:16],'createdAt':datetime.now(timezone.utc).isoformat(),'release':controller.environment['PORTAL_RELEASE'],'sourceCommit':controller.source_commit(),'files':{name:{'bytes':(temporary/name).stat().st_size,'sha256':file_digest(temporary/name)} for name in ['database.enc','files.enc']}}
        manifest['postgresStorageFamily']=controller.environment.get('POSTGRES_STORAGE_FAMILY')
        manifest['authentication']=signature(manifest,key)
        private_write(temporary/'manifest.json',json.dumps(manifest,indent=2)+'\n')
        # Read and authenticate both encrypted streams before considering the backup complete.
        authenticate(temporary,key)
        for name in ['database.enc','files.enc']:consume_encrypted(controller.codec('verify'),temporary/name,key,environment=controller.process_environment)
        temporary.rename(target)
        private_write(controller.directory/'last-backup.json',json.dumps({'path':str(target),'createdAt':manifest['createdAt'],'keyId':manifest['keyId']},indent=2)+'\n')
        return target
    finally:
        if paused and resume_services:controller.run('up','-d','--no-build','--no-deps','app','worker','proxy')


def restore_backup(controller,directory,confirmation,replace=False,key_file=None):
    if confirmation!=controller.installation['hostname']:raise ConfigurationError('Confirmation must equal the configured hostname')
    key=Path(key_file).read_bytes() if key_file else controller.backup_key()
    if len(key)!=32:raise ConfigurationError('Recovery key must contain exactly 32 bytes')
    directory=Path(directory).resolve();manifest=authenticate(directory,key)
    if manifest.get('postgresStorageFamily')!=controller.environment.get('POSTGRES_STORAGE_FAMILY'):
        raise ConfigurationError('PostgreSQL storage family differs or is missing; use an isolated logical migration, not an automatic restore across families')
    if not controller.image_available(manifest['release']):raise ConfigurationError('Load the matching API/worker release images before restoring this snapshot')
    # No service is stopped and no data is changed until both authentication tags pass.
    for name in ['database.enc','files.enc']:consume_encrypted(controller.codec('verify'),directory/name,key,environment=controller.process_environment)
    if not replace:raise ConfigurationError('Restoring replaces database/files; explicitly add --replace after confirming the recovery plan')
    safety=make_backup(controller,controller.backup_directory(),resume_services=False)
    controller.run('stop','proxy','worker','app')
    controller.use_release(manifest['release'])
    try:
        consume_encrypted(controller.codec('decrypt'),directory/'database.enc',key,controller.command('exec','-T','postgres','pg_restore','-U','postgres','--dbname=portal','--role=portal_owner','--clean','--if-exists','--no-owner','--no-acl','--single-transaction','--exit-on-error'),controller.process_environment)
        consume_encrypted(controller.codec('decrypt'),directory/'files.enc',key,controller.command('run','--rm','-T','--no-deps','restore-tool','operations/volume_archive.py','unpack','--replace'),controller.process_environment)
        controller.grant_runtime_permissions()
        controller.recover(invalidate=True)
        controller.run('run','--rm','-T','--no-deps','app','python','backend/manage.py','verify_private_files')
    except Exception as error:
        raise ConfigurationError('Restore failed; services remain stopped. The pre-restore backup is '+str(safety)) from error
    try:
        controller.run('up','-d','--no-build','--no-deps','app','worker','proxy')
        controller.wait_ready()
        controller.runtime_check(require_backup=False)
        current=make_backup(controller,controller.backup_directory())
        controller.wait_ready();controller.runtime_check()
    except Exception as error:
        controller.run('stop','proxy','worker','app')
        raise ConfigurationError('Post-restore verification failed; services stopped. The pre-restore backup is '+str(safety)) from error
    return {'postRestoreBackup':str(current),'restored':str(directory),'safetyBackup':str(safety),'release':manifest['release']}


def prune_backups(parent,key,days=14,keep=3,now=None):
    """Remove only authenticated old snapshots made with this installation's key."""
    import re
    from datetime import timedelta
    if days<7 or keep<3:raise ConfigurationError('Keep at least seven days and three complete snapshots')
    now=now or datetime.now(timezone.utc);candidates=[]
    for directory in Path(parent).iterdir():
        if directory.is_symlink() or not directory.is_dir() or not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{6}',directory.name):continue
        try:
            manifest=authenticate(directory,key)
            stamp=datetime.fromisoformat(manifest['createdAt'])
            if stamp.tzinfo is None:continue
            candidates.append((stamp,directory))
        except (OSError,ValueError,KeyError):continue
    candidates.sort(reverse=True);removed=0
    for stamp,directory in candidates[keep:]:
        if stamp<now-timedelta(days=days):shutil.rmtree(directory);removed+=1
    return removed
