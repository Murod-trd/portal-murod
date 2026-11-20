"""One-command disposable Linux/Docker acceptance; never targets a live portal.

Secrets and diagnostic logs remain private. Only evidence/ is shareable.
Technical success cannot certify independent security or company acceptance.
"""
import argparse
import json
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from operations.configuration import configure,private_write,ConfigurationError
from operations.control import Controller,check_project_ownership,verify_package,operation_lock,preflight
from operations.backup import make_backup

STAGES={'prepare-isolated-labs','native-postgres-tests-and-benchmark','installation-and-live-av-ocr',
        'https-business-workflows','independent-volume-restoration','final-image-scan','base-image-scan',
        'prepared-platform-scan'}


class StageFailed(Exception):
    pass


def copy_release(source,destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):raise ConfigurationError('Acceptance workspace must be separate from source')
    if destination.exists():raise ConfigurationError('Refusing to overwrite an existing lab checkout')
    manifest=verify_package(source)
    destination.mkdir(mode=0o700,parents=True)
    for name in [*manifest['sha256'],'RELEASE-MANIFEST.json']:
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,target)
    verify_package(destination)


def host_blockers():
    errors=[]
    if platform.system()!='Linux' or platform.machine() not in {'x86_64','AMD64'}:errors.append('A Linux x86_64 host is required')
    if sys.version_info<(3,10):errors.append('Python 3.10+ is required')
    for binary in ['docker','openssl']:
        if shutil.which(binary) is None:errors.append(binary+' is unavailable')
    return errors


def parse_marker(path,marker):
    found=None
    with Path(path).open(errors='replace') as stream:
        for line in stream:
            if line.startswith(marker):found=json.loads(line[len(marker):])
    if not isinstance(found,dict):raise StageFailed('Required structured check result was not produced')
    return found


class Acceptance:
    def __init__(self,work,source=ROOT,keep_lab=False):
        self.source=Path(source).resolve();self.work=Path(work).resolve()
        if self.work.is_relative_to(self.source) or self.source.is_relative_to(self.work):raise ConfigurationError('Use a new acceptance directory outside source')
        self.work.mkdir(mode=0o700,parents=True,exist_ok=False)
        (self.work/'private').mkdir(mode=0o700);(self.work/'evidence').mkdir(mode=0o700)
        self.run_id=secrets.token_hex(12);self.keep_lab=keep_lab;self.controllers=[];self.http_state=None
        self.report={'format':1,'checkedAt':datetime.now(timezone.utc).isoformat(),'runId':self.run_id,
                     'technicalChecksPassed':False,'productionApproved':False,'stages':[],'results':{},
                     'externalGates':{'browserE2E':'NOT_VERIFIED','independentPentest':'NOT_PERFORMED',
                                      'companyVpnDnsTls':'NOT_VERIFIED','secondPhysicalHostRecovery':'NOT_VERIFIED',
                                      'faultAndHttpLoad':'NOT_VERIFIED','realCompanyOcrCorpus':'NOT_ACCEPTED'}}

    def execute(self,name,arguments,*,cwd=None,environment=None,input_text=None,timeout=3600):
        if not re.fullmatch(r'[a-z0-9-]+',name):raise ConfigurationError('Invalid diagnostic stage name')
        log=self.work/'private'/(name+'.log')
        with log.open('wb') as stream:
            try:
                options={'input':input_text,'text':True}if input_text is not None else {'stdin':subprocess.DEVNULL}
                done=subprocess.run(arguments,cwd=cwd or self.source,env=environment,stdout=stream,stderr=subprocess.STDOUT,
                                    timeout=timeout,check=False,**options)
            except (OSError,subprocess.SubprocessError) as error:raise StageFailed(type(error).__name__)from error
        if done.returncode:raise StageFailed('Command failed; private diagnostic log: '+log.name)
        return log

    def save(self):
        self.report['notExecuted']=sorted(STAGES-{s['name']for s in self.report['stages']})
        private_write(self.work/'evidence/report.json',json.dumps(self.report,indent=2)+'\n')

    def stage(self,name,operation):
        started=time.monotonic();print('Acceptance stage: '+name,flush=True)
        try:
            value=operation()
            self.report['stages'].append({'name':name,'status':'passed','seconds':round(time.monotonic()-started,3)})
            return value
        except Exception as error:
            failure={'name':name,'status':'failed','errorType':type(error).__name__,'seconds':round(time.monotonic()-started,3)}
            if isinstance(error,StageFailed):failure['reason']=str(error)
            self.report['stages'].append(failure)
            return None
        finally:self.save()

    def prepare(self):
        verify_package(self.source)
        tls=self.work/'private/tls';tls.mkdir(mode=0o700)
        self.execute('test-tls-validity',['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','30',
                     '-keyout',str(tls/'key.pem'),'-out',str(tls/'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'])
        for name,port in [('source',18443),('recovery',18444)]:
            checkout=self.work/name;copy_release(self.source,checkout)
            configure(checkout,'https://localhost:'+str(port),'127.0.0.1',['127.0.0.1/32','172.16.0.0/12'],
                      tls/'cert.pem',tls/'key.pem',backup_directory=self.work/'private'/(name+'-backups'),
                      ca_file=tls/'cert.pem',project_name='ti-accept-'+name+'-'+self.run_id)
            controller=Controller(checkout)
            # Before the first Docker build/start; failed contexts never enter cleanup.
            preflight(controller)
            self.controllers.append(controller)
        return True

    def native(self):
        controller=self.controllers[0];env,cwd=controller.process_environment,controller.root
        self.execute('build-native',controller.command('build','postgres','app','worker','verification'),cwd=cwd,environment=env)
        self.execute('start-native-postgres',controller.command('up','-d','--wait','--wait-timeout','180','postgres'),cwd=cwd,environment=env)
        log=self.execute('native-postgres-tests',controller.command('run','--rm','-T','--no-deps','-e',
                         'PORTAL_ACCEPTANCE_RUN_ID='+self.run_id,'verification'),cwd=cwd,environment=env,timeout=1500)
        value=parse_marker(log,'PORTAL_NATIVE_CHECKS=');self.report['results']['nativePostgres']=value
        if not value.get('tests',{}).get('passed') or not value.get('benchmark',{}).get('passed'):raise StageFailed('Native tests or benchmark failed')
        return True

    def install(self,index):
        controller=self.controllers[index];name='source'if index==0 else'recovery';password=secrets.token_urlsafe(32)
        private_write(self.work/'private'/(name+'-credentials.json'),json.dumps({'username':'acceptance.admin','temporaryPassword':password})+'\n')
        try:
            self.execute('install-'+name,[sys.executable,'portalctl','install','--admin-login','acceptance.admin',
                         '--admin-name','Synthetic Acceptance Administrator','--password-stdin'],cwd=controller.root,
                         environment=controller.process_environment,input_text=password+'\n')
        finally:
            status=controller.directory/'last-check.json'
            if status.exists():self.report['results'][name+'Installation']=json.loads(status.read_text())
        return True

    def http_workflows(self):
        from operations.http_acceptance import workflows
        controller=self.controllers[0];credentials=json.loads((self.work/'private/source-credentials.json').read_text())
        report,self.http_state=workflows(controller.installation['origin'],self.work/'private/tls/cert.pem',credentials,self.run_id)
        private_write(self.work/'private/acceptance-accounts.json',json.dumps(self.http_state['privateAccounts'])+'\n')
        self.report['results']['httpsWorkflows']=report
        return True

    def restore(self):
        source,target=self.controllers
        with operation_lock(source.directory):
            snapshot=make_backup(source,source.backup_directory(),resume_services=False)
            original=json.loads(source.run('run','--rm','-T','--no-deps','app','python','backend/manage.py','recovery_inventory',capture=True))
            source.run('stop','postgres','clamav')
        self.install(1);started=time.monotonic()
        self.execute('restore-independent-volumes',[sys.executable,'portalctl','restore',str(snapshot),'--confirm','localhost',
                     '--replace','--key-file',str(source.directory/'keys/backup.key')],cwd=target.root,environment=target.process_environment)
        observed=json.loads(target.run('exec','-T','app','python','backend/manage.py','recovery_inventory',capture=True))
        if original!=observed:raise StageFailed('Restored business record digests differ')
        self.execute('post-restore-pipeline',[sys.executable,'portalctl','check','--pipeline','--files'],cwd=target.root,environment=target.process_environment)
        if not self.http_state:raise StageFailed('No authenticated workflow state for restoration check')
        from operations.http_acceptance import verify_restored
        self.report['results']['restoredHttpsSessionAndFiles']=verify_restored(self.http_state,target.installation['origin'])
        self.report['results']['isolatedRestore']={'passed':True,'recordDigestsMatched':True,'fileIntegrityAndPipeline':True,
            'seconds':round(time.monotonic()-started,3),'scope':'Independent Compose project/database/files volumes on the same host, not a second physical server'}
        return True

    def scan(self,scope):
        tool=self.work/'private/trivy'
        if not tool.exists():self.execute('install-trivy',[sys.executable,str(self.source/'operations/install_trivy.py'),'--output',str(tool)])
        prefix=self.controllers[0].environment['PORTAL_IMAGE_PREFIX'];directory=self.work/'evidence'/('image-scans-'+scope)
        command=[sys.executable,str(self.source/'operations/security_scan.py'),'--trivy',str(tool),'--scope',scope,
                 '--output',str(directory),'--cache',str(self.work/'private/trivy-cache'),'--image-prefix',prefix,'--strict']
        try:self.execute('scan-'+scope,command,timeout=3600)
        finally:
            summary=directory/'summary.json'
            if summary.exists():self.report['results']['imageScan-'+scope]=json.loads(summary.read_text())
        return True

    def cleanup(self):
        for index,controller in enumerate(self.controllers):
            if self.keep_lab:self.report['results']['labsRetained']=True;continue
            try:
                with operation_lock(controller.directory):
                    check_project_ownership(controller)
                    self.execute('cleanup-'+str(index),controller.command('down','--volumes','--remove-orphans'),
                                 cwd=controller.root,environment=controller.process_environment,timeout=180)
            except Exception as error:self.report['stages'].append({'name':'cleanup-'+str(index),'status':'failed','errorType':type(error).__name__})

    def run(self):
        blockers=host_blockers()
        if blockers:
            self.report['hostBlockers']=blockers;self.report['stages'].append({'name':'host','status':'blocked'});self.save();return 2
        try:
            if self.stage('prepare-isolated-labs',self.prepare):
                self.stage('native-postgres-tests-and-benchmark',self.native)
                if self.stage('installation-and-live-av-ocr',lambda:self.install(0)):
                    if self.stage('https-business-workflows',self.http_workflows):self.stage('independent-volume-restoration',self.restore)
                self.stage('final-image-scan',lambda:self.scan('final'));self.stage('base-image-scan',lambda:self.scan('base'))
                self.stage('prepared-platform-scan',lambda:self.scan('prepared'))
        finally:
            self.cleanup()
            passed={s['name']for s in self.report['stages']if s['status']=='passed'}
            self.report['technicalChecksPassed']=STAGES<=passed and all(s['status']=='passed'for s in self.report['stages'])
            self.save()
        return 0 if self.report['technicalChecksPassed'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir',required=True,help='New private directory outside source')
    parser.add_argument('--keep-lab',action='store_true',help='Retain only the new disposable lab for manual UI review')
    args=parser.parse_args()
    try:
        run=Acceptance(args.work_dir,keep_lab=args.keep_lab);code=run.run()
        print(json.dumps({'technicalChecksPassed':run.report['technicalChecksPassed'],'productionApproved':False,
                          'report':str(run.work/'evidence/report.json'),'hostBlockers':run.report.get('hostBlockers',[])}))
        return code
    except (ConfigurationError,OSError) as error:
        print('Acceptance was not started: '+str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
