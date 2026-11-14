"""Single-server operations entrypoint. Requires only Python 3 and Docker Compose on the host."""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime,timezone
from contextlib import contextmanager
from pathlib import Path
from .configuration import ConfigurationError,configure,private_write,read_environment,postgres_storage_identity,validate_postgres_lock

ROOT=Path(__file__).resolve().parents[1]

class OperationBusy(ConfigurationError):pass


def verify_package(root):
    manifest=Path(root)/'RELEASE-MANIFEST.json'
    if not manifest.is_file():raise ConfigurationError('Use the packaged release, including RELEASE-MANIFEST.json')
    value=json.loads(manifest.read_text())
    for name,expected in value['sha256'].items():
        path=Path(root)/name
        if not path.resolve().is_relative_to(Path(root).resolve()) or not path.is_file() or path.is_symlink():raise ConfigurationError('Release file missing/unsafe: '+name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ConfigurationError('Release file has changed: '+name)
    return value


class Controller:
    def __init__(self,root=ROOT):
        self.root=Path(root).resolve();self.directory=self.root/'.deploy'
        self.environment=read_environment(self.directory/'portal.env')
        self.installation=json.loads((self.directory/'installation.json').read_text())
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,62}',self.environment.get('PORTAL_IMAGE_PREFIX','toshkent-invest-portal')):
            raise ConfigurationError('Invalid application image prefix')
        self.environment_path=self.directory/'portal.env'
        self.process_environment={key:value for key,value in os.environ.items() if key not in self.environment and not key.startswith('COMPOSE_')}
    def command(self,*arguments):
        return ['docker','compose','--env-file',str(self.environment_path),'-f',str(self.root/'compose.yaml'),*arguments]
    def run(self,*arguments,capture=False,timeout=3600,interactive=False):
        done=subprocess.run(self.command(*arguments),cwd=self.root,env=self.process_environment,stdin=None if interactive else subprocess.DEVNULL,check=False,timeout=timeout,stdout=subprocess.PIPE if capture else None,stderr=subprocess.DEVNULL if capture else None)
        if done.returncode:raise ConfigurationError('Container operation failed: '+arguments[0])
        return done.stdout if capture else None
    def codec(self,mode):return self.command('run','--rm','-T','--no-deps','backup-tool','operations/crypto_stream.py',mode)
    def backup_key(self):
        path=self.directory/'keys/backup.key'
        if path.stat().st_mode & 0o077:raise ConfigurationError('Backup key permissions must be 0600')
        key=path.read_bytes()
        if len(key)!=32:raise ConfigurationError('Invalid recovery key')
        return key
    def backup_directory(self):return Path(self.installation['backupDirectory'])
    def source_commit(self):
        try:return json.loads((self.root/'RELEASE-MANIFEST.json').read_text())['sourceCommit']
        except (OSError,KeyError):return 'source-checkout'
    def use_release(self,release):
        if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}',release):raise ConfigurationError('Invalid release identifier')
        self.environment['PORTAL_RELEASE']=release
        private_write(self.environment_path,'\n'.join(f'{key}={value}' for key,value in self.environment.items())+'\n')
    def image_available(self,release):
        if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}',release):return False
        return all(subprocess.run(['docker','image','inspect',self.environment.get('PORTAL_IMAGE_PREFIX','toshkent-invest-portal')+'-'+kind+':'+release],env=self.process_environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False).returncode==0 for kind in ['api','worker'])
    def recover(self,invalidate=False):
        arguments=['run','--rm','-T','--no-deps','app','python','backend/manage.py','recover_processing','--workers-stopped']
        if invalidate:arguments.append('--invalidate-sessions')
        self.run(*arguments)
    def ensure_search_extension(self):
        self.run('exec','-T','postgres','psql','-U','postgres','-d','portal','-v','ON_ERROR_STOP=1','-c','CREATE EXTENSION IF NOT EXISTS pg_trgm')
    def grant_runtime_permissions(self):
        sql='GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO portal_app; GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO portal_app;'
        self.run('exec','-T','postgres','psql','-U','postgres','-d','portal','-v','ON_ERROR_STOP=1','-c',sql)
    def runtime_check(self,pipeline=False,files=False,require_backup=True):
        args=['run','--rm','-T','--no-deps','app','python','backend/manage.py','verify_deployment']
        if pipeline:args.append('--pipeline')
        if files:args.append('--files')
        done=subprocess.run(self.command(*args),cwd=self.root,env=self.process_environment,capture_output=True,timeout=1200,check=False)
        try:report=json.loads(done.stdout)
        except ValueError as error:raise ConfigurationError('Technical check did not produce a valid report') from error
        private_write(self.directory/'last-check.json',json.dumps(report,indent=2)+'\n')
        if not done.returncode:
            from .http_probe import probe
            ca=self.directory/'tls/ca.pem'
            report['checks'].update(probe(self.installation['origin'],ca if ca.exists() else None))
            private_write(self.directory/'last-check.json',json.dumps(report,indent=2)+'\n')
        if require_backup:
            fresh=False
            try:
                backup=json.loads((self.directory/'last-backup.json').read_text())
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(backup['createdAt'])).total_seconds()
                snapshot=Path(backup['path']);manifest=json.loads((snapshot/'manifest.json').read_text())
                from .backup import signature
                authentication=manifest.pop('authentication','')
                import hmac
                valid=isinstance(authentication,str) and hmac.compare_digest(authentication,signature(manifest,self.backup_key()))
                present=set(manifest.get('files',{}))=={'database.enc','files.enc'} and all((snapshot/name).is_file() and (snapshot/name).stat().st_size==metadata['bytes'] for name,metadata in manifest['files'].items())
                fresh=valid and present and -3600<=age<=36*3600
            except (OSError,ValueError,KeyError,TypeError):pass
            report['checks']['backupFresh']=fresh
            private_write(self.directory/'last-check.json',json.dumps(report,indent=2)+'\n')
        if done.returncode or not all(report.get('checks',{}).values()):
            failed=[key for key,value in report.get('checks',{}).items() if not value]
            raise ConfigurationError('Technical checks failed: '+', '.join(failed))
        return report
    def wait_ready(self,timeout=900):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            command=self.command('exec','-T','app','python','operations/health.py','api-ready')
            done=subprocess.run(command,cwd=self.root,env=self.process_environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=False)
            if done.returncode==0:return
            time.sleep(5)
        raise ConfigurationError('Readiness timed out. Check ClamAV signatures, worker health and private storage.')


@contextmanager
def operation_lock(directory):
    try:import fcntl
    except ImportError as error:raise ConfigurationError('This release requires Linux x86_64. On Windows Server, use a Linux virtual machine.') from error
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    with open(directory/'operation.lock','a') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise OperationBusy('Another install/update/backup/restore is running') from error
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def inspect_host():
    report={'os':platform.system(),'architecture':platform.machine(),'python':platform.python_version(),'dockerClient':bool(shutil.which('docker'))}
    if platform.system()=='Linux':
        report['memoryGiB']=round(os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')/1024**3,1)
        report['cpuCount']=os.cpu_count();report['freeDiskGiB']=round(shutil.disk_usage(ROOT).free/1024**3,1)
    return report



def check_project_ownership(controller):
    expected=controller.installation['installationId']
    ids=controller.run('ps','-aq',capture=True).decode().split()
    for identifier in ids:
        checked=subprocess.run(['docker','inspect','--format','{{json .Config.Labels}}',identifier],env=controller.process_environment,capture_output=True,timeout=15,check=False)
        labels=json.loads(checked.stdout) if checked.returncode==0 else None
        if not labels or labels.get('org.toshkent.portal.installation')!=expected:raise ConfigurationError('An existing container belongs to a different installation; nothing was replaced')
    project=controller.environment['COMPOSE_PROJECT_NAME']
    for name in ['postgres-data','private-files','clamav-data','backup-work']:
        checked=subprocess.run(['docker','volume','inspect','--format','{{json .Labels}}',project+'_'+name],env=controller.process_environment,capture_output=True,timeout=15,check=False)
        if checked.returncode==0:
            labels=json.loads(checked.stdout)
            if not labels or labels.get('org.toshkent.portal.installation')!=expected:raise ConfigurationError('An existing data volume belongs to a different installation; manual migration is required')


def preflight(controller,package=True):
    if platform.system()!='Linux' or platform.machine() not in {'x86_64','AMD64'}:
        raise ConfigurationError('This release requires Linux x86_64. On Windows Server, use a Linux virtual machine.')
    if sys.version_info<(3,10):raise ConfigurationError('Python 3.10+ is required on the host')
    if package:verify_package(controller.root)
    if not shutil.which('docker'):raise ConfigurationError('IT must install Docker Engine with Compose v2 first')
    context=subprocess.run(['docker','context','inspect'],capture_output=True,timeout=15,check=False)
    try:endpoint=json.loads(context.stdout)[0]['Endpoints']['docker']['Host']
    except (ValueError,KeyError,IndexError):raise ConfigurationError('Cannot inspect the local Docker context')
    if endpoint!='unix:///var/run/docker.sock' or os.environ.get('DOCKER_HOST',endpoint)!=endpoint:raise ConfigurationError('Use the standard local Docker Engine on the target Linux server')
    info=subprocess.run(['docker','info','--format','{{json .}}'],capture_output=True,timeout=20,check=False)
    if info.returncode:raise ConfigurationError('Docker Engine is unavailable to this operator')
    docker=json.loads(info.stdout)
    if any('name=rootless' in item or 'name=userns' in item for item in docker.get('SecurityOptions',[])):raise ConfigurationError('Use the documented standard local Docker Engine profile in a dedicated VM')
    if docker.get('OSType')!='linux' or docker.get('Architecture') not in {'x86_64','amd64'}:raise ConfigurationError('Docker must run Linux/amd64 containers')
    if docker.get('MemTotal',0)<12*1024**3 or docker.get('NCPU',0)<4:raise ConfigurationError('Allocate at least 4 vCPU and 12 GiB RAM; 16 GiB is recommended')
    if shutil.disk_usage(controller.root).free<40*1024**3:raise ConfigurationError('At least 40 GiB free disk is required for installation')
    version=subprocess.run(['docker','compose','version','--short'],capture_output=True,text=True,timeout=15,check=False)
    match=re.search(r'(\d+)\.(\d+)',version.stdout)
    if version.returncode or not match or tuple(map(int,match.groups()))<(2,24):raise ConfigurationError('Docker Compose v2.24+ is required')
    with socket.socket() as probe:
        try:probe.bind((controller.installation['bind'],0))
        except OSError as error:raise ConfigurationError('Configured private bind address is not assigned to this server') from error
    controller.backup_directory().mkdir(parents=True,exist_ok=True,mode=0o700)
    controller.run('config','--quiet',capture=True)
    check_project_ownership(controller)
    return {'host':'compatible','packageIntegrity':'verified' if package else 'not-checked'}


def install(controller,args):
    if (controller.directory/'deployment-complete.json').exists():raise ConfigurationError('An installation is already active. Use update or check; reinstall was not started.')
    preflight(controller)
    try:
        controller.run('pull','clamav','proxy')
        controller.run('build','postgres','frontend-tools','app','worker')
        controller.run('up','-d','--wait','--wait-timeout','900','postgres','clamav')
        controller.ensure_search_extension()
        controller.run('run','--rm','--no-deps','migrate')
        controller.run('up','-d','--no-build','--no-deps','--wait','--wait-timeout','120','app')
        controller.run('up','-d','--no-build','--no-deps','--wait','--wait-timeout','180','worker','proxy')
        controller.wait_ready()
        if args.admin_login:
            bootstrap=['exec']
            if args.password_stdin:bootstrap.append('-T')
            bootstrap+=['app','python','backend/manage.py','bootstrap_admin','--if-missing','--username',args.admin_login,'--name',args.admin_name]
            if args.password_stdin:bootstrap.append('--password-stdin')
            controller.run(*bootstrap,interactive=True)
        report=controller.runtime_check(pipeline=True,files=True,require_backup=False)
        if not report['checks']['activeAdministrator']:
            raise ConfigurationError('Create the first individual administrator using portalctl bootstrap, then rerun portalctl check --pipeline')
        from .backup import make_backup
        snapshot=make_backup(controller,controller.backup_directory())
        controller.wait_ready()
        report=controller.runtime_check()
        report['initialBackup']=str(snapshot)
        private_write(controller.directory/'deployment-complete.json',json.dumps({'release':controller.environment['PORTAL_RELEASE'],'completedAt':datetime.now(timezone.utc).isoformat()})+'\n')
        return report
    except BaseException:
        try:controller.run('stop','proxy')
        except Exception:pass
        raise

def validate_postgres_update(previous, target, previous_family=None, target_family=None):
    """A shared data volume must keep both PostgreSQL major and OS/libc family."""
    old=postgres_storage_identity(previous,previous_family);new=postgres_storage_identity(target,target_family)
    if not old or not new or old!=new:
        raise ConfigurationError('PostgreSQL major, OS or package family change requires dump/restore into a new volume; in-place update refused')


def update(controller):
    from .backup import make_backup
    preflight(controller)
    image_lock=json.loads((controller.root/'deploy/images.lock.json').read_text());locked=image_lock['images']
    family=validate_postgres_lock(image_lock)
    validate_postgres_update(controller.environment['POSTGRES_IMAGE'],locked['postgres'],controller.environment.get('POSTGRES_STORAGE_FAMILY'),family)
    previous=controller.environment.copy()
    release=(controller.root/'RELEASE').read_text().strip()
    if release==previous['PORTAL_RELEASE']:raise ConfigurationError('The same release is already configured; use check/restart rather than rebuilding it in place')
    # Build ahead of downtime, preserving the old tag for recovery.
    controller.use_release(release)
    for name,value in locked.items():controller.environment[name.upper()+'_IMAGE']=value
    controller.environment['POSTGRES_STORAGE_FAMILY']=family
    private_write(controller.environment_path,'\n'.join(f'{key}={value}' for key,value in controller.environment.items())+'\n')
    try:
        controller.run('pull','clamav','proxy');controller.run('build','postgres','frontend-tools','app','worker')
    finally:
        controller.environment=previous
        private_write(controller.environment_path,'\n'.join(f'{key}={value}' for key,value in previous.items())+'\n')
    backup=make_backup(controller,controller.backup_directory())
    controller.run('stop','proxy','worker','app')
    controller.use_release(release)
    for name,value in locked.items():controller.environment[name.upper()+'_IMAGE']=value
    controller.environment['POSTGRES_STORAGE_FAMILY']=family
    private_write(controller.environment_path,'\n'.join(f'{key}={value}' for key,value in controller.environment.items())+'\n')
    try:
        controller.run('up','-d','--wait','--wait-timeout','900','postgres','clamav');controller.ensure_search_extension();controller.run('run','--rm','--no-deps','migrate')
        controller.run('up','-d','--no-build','--no-deps','app','worker','proxy');controller.wait_ready()
        return {'updated':release,'backup':str(backup),'checks':controller.runtime_check(pipeline=True,files=True)['checks']}
    except Exception as error:
        controller.run('stop','proxy','worker','app')
        raise ConfigurationError('Update failed; services stopped. Restore the pre-update snapshot: '+str(backup)) from error


def main():
    parser=argparse.ArgumentParser(description='Toshkent Invest: installation, checks and encrypted recovery. No JavaScript backend.')
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('inspect-host')
    config=sub.add_parser('configure');config.add_argument('--origin',required=True);config.add_argument('--bind',required=True);config.add_argument('--allow',action='append',required=True);config.add_argument('--tls-cert',required=True);config.add_argument('--tls-key',required=True);config.add_argument('--backup-dir',required=True);config.add_argument('--ca-file');config.add_argument('--project-name',default='toshkent-invest-portal')
    sub.add_parser('doctor')
    install_parser=sub.add_parser('install');install_parser.add_argument('--admin-login',required=True);install_parser.add_argument('--admin-name',required=True);install_parser.add_argument('--password-stdin',action='store_true')
    bootstrap=sub.add_parser('bootstrap');bootstrap.add_argument('--username',required=True);bootstrap.add_argument('--name',required=True);bootstrap.add_argument('--password-stdin',action='store_true')
    check=sub.add_parser('check');check.add_argument('--pipeline',action='store_true');check.add_argument('--files',action='store_true')
    sub.add_parser('backup');sub.add_parser('update');sub.add_parser('status')
    schedule=sub.add_parser('schedule');schedule.add_argument('--output-dir',default='/etc/systemd/system');schedule.add_argument('--enable',action='store_true')
    restore=sub.add_parser('restore');restore.add_argument('snapshot');restore.add_argument('--confirm',required=True);restore.add_argument('--replace',action='store_true');restore.add_argument('--key-file')
    verify=sub.add_parser('verify-backup');verify.add_argument('snapshot')
    args=parser.parse_args()
    try:
        if args.action=='inspect-host':result=inspect_host()
        elif args.action=='configure':
            result=configure(ROOT,args.origin,args.bind,args.allow,args.tls_cert,args.tls_key,backup_directory=args.backup_dir,ca_file=args.ca_file,project_name=args.project_name)
        else:
            controller=Controller()
            if platform.system()!='Linux':raise ConfigurationError('Run operations inside a Linux VM/server')
            with operation_lock(controller.directory):
                if args.action=='doctor':result=preflight(controller)
                elif args.action=='install':result=install(controller,args)
                elif args.action=='bootstrap':
                    command=['exec']+(['-T'] if args.password_stdin else [])+['app','python','backend/manage.py','bootstrap_admin','--username',args.username,'--name',args.name]+(['--password-stdin'] if args.password_stdin else [])
                    controller.run(*command,interactive=True);result={'administrator':'created'}
                elif args.action=='check':result=controller.runtime_check(args.pipeline,args.files)
                elif args.action=='status':
                    controller.run('ps');result={'origin':controller.installation['origin'],'lastCheck':json.loads((controller.directory/'last-check.json').read_text()) if (controller.directory/'last-check.json').exists() else None}
                elif args.action=='backup':
                    from .backup import make_backup
                    snapshot=make_backup(controller,controller.backup_directory())
                    controller.wait_ready();controller.runtime_check()
                    from .backup import prune_backups
                    removed=prune_backups(controller.backup_directory(),controller.backup_key(),int(controller.environment.get('BACKUP_RETENTION_DAYS','14')))
                    result={'snapshot':str(snapshot),'oldSnapshotsRemoved':removed}
                elif args.action=='restore':
                    from .backup import restore_backup
                    result=restore_backup(controller,args.snapshot,args.confirm,args.replace,args.key_file)
                elif args.action=='verify-backup':
                    from .backup import authenticate,consume_encrypted
                    manifest=authenticate(args.snapshot,controller.backup_key())
                    for name in manifest['files']:consume_encrypted(controller.codec('verify'),Path(args.snapshot)/name,controller.backup_key(),environment=controller.process_environment)
                    result={'authenticated':True,'release':manifest['release']}
                elif args.action=='schedule':
                    from .service_units import write_units
                    if args.enable and (os.geteuid()!=0 or Path(args.output_dir)!=Path('/etc/systemd/system')):raise ConfigurationError('Enabling system timers requires root and /etc/systemd/system')
                    names=write_units(controller.root,args.output_dir)
                    if args.enable:
                        for command in [['systemctl','daemon-reload'],['systemctl','enable','--now','toshkent-portal-backup.timer','toshkent-portal-health.timer']]:
                            subprocess.run(command,check=True,timeout=30)
                    result={'unitFiles':names,'enabled':args.enable}
                elif args.action=='update':result=update(controller)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except OperationBusy as error:
        if args.action in {'check','status'}:
            print(json.dumps({'status':'planned-maintenance','ready':False}));return
        print(str(error),file=sys.stderr);raise SystemExit(1)
    except (ConfigurationError,OSError,ValueError,subprocess.SubprocessError) as error:
        print('Операция не завершена: '+str(error),file=sys.stderr);raise SystemExit(1)
