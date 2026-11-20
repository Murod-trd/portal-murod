import ipaddress
import json
import os
import re
import shutil
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

class ConfigurationError(ValueError):pass

def postgres_storage_identity(reference,family=None):
    """Identify the storage implementation, not just the rootfs image tag."""
    if family:
        declared=re.fullmatch(r'postgresql(\d+)-apk-alpine(\d+\.\d+)',family)
        image=re.fullmatch(r'(?:library/)?alpine:(\d+\.\d+)@sha256:[a-f0-9]{64}',reference)
        if declared and image and declared[2]==image[1]:return declared[1],declared[2],'apk'
    else:
        image=re.search(r'postgres:(\d+)(?:\.\d+)*-([a-zA-Z0-9_.-]+)@sha256:[a-f0-9]{64}$',reference)
        if image:return image[1],image[2],'upstream'
    return None


def validate_postgres_lock(lock):
    family=lock.get('postgresStorageFamily')
    identity=postgres_storage_identity(lock['images']['postgres'],family) if family else None
    if not identity:raise ConfigurationError('Image lock must identify the PostgreSQL package/storage family')
    package='postgresql'+identity[0];packages=lock.get('postgresPackages',{});version=packages.get(package,'')
    if (not isinstance(version,str) or not re.fullmatch(re.escape(identity[0])+r'\.\d+-r\d+',version)
            or any(packages.get(package+suffix)!=version for suffix in ['-client','-contrib'])):
        raise ConfigurationError('PostgreSQL package versions must match the declared storage family')
    return family


def private_write(path,data,mode=0o600):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.with_name(path.name+'.new-'+secrets.token_hex(4))
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(data.encode() if isinstance(data,str) else data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)

def validate_origin(value):
    if any(ord(character)<=32 or ord(character)==127 for character in value):
        raise ConfigurationError('Whitespace and control characters are not allowed in the origin')
    parsed=urlsplit(value)
    try:port=parsed.port or 443
    except ValueError as error:raise ConfigurationError('Invalid HTTPS port') from error
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
        raise ConfigurationError('Use an exact HTTPS origin without a path or credentials')
    hostname=parsed.hostname
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?',hostname) or '..' in hostname or len(hostname)>253:
        raise ConfigurationError('Use an ASCII DNS hostname or IPv4 address')
    if not 1<=port<=65535:raise ConfigurationError('Invalid port')
    return 'https://'+hostname+(':'+str(port) if port!=443 else ''),hostname,port

def validate_bind(value):
    try:address=ipaddress.IPv4Address(value)
    except ValueError as error:raise ConfigurationError('Bind must be an IPv4 address on the server') from error
    private=[ipaddress.ip_network(network) for network in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8']]
    if not any(address in network for network in private):raise ConfigurationError('Bind only a private office/VPN or loopback address')
    return str(address)

def validate_networks(values):
    networks=[]
    for value in values:
        try:network=ipaddress.ip_network(value,strict=True)
        except ValueError as error:raise ConfigurationError('Use an exact office/VPN CIDR') from error
        if network.prefixlen==0 or network.is_multicast or network.is_unspecified:
            raise ConfigurationError('An unrestricted network is not allowed')
        networks.append(str(network))
    if not networks:raise ConfigurationError('At least one office/VPN network is required')
    return sorted(set(networks+['127.0.0.1/32','::1/128']))

def read_environment(path):
    values={}
    for line in Path(path).read_text().splitlines():
        if line and not line.startswith('#'):
            key,separator,value=line.partition('=')
            if not separator or not re.fullmatch('[A-Z_][A-Z_0-9]*',key):raise ConfigurationError('Invalid generated environment file')
            values[key]=value
    return values

def validate_certificate(cert,key,hostname):
    def run(arguments):
        done=subprocess.run(arguments,capture_output=True,timeout=15,check=False,env={**os.environ,'LC_ALL':'C'})
        if done.returncode:raise ConfigurationError('TLS certificate/key check failed')
        return done.stdout
    cert=Path(cert).resolve();key=Path(key).resolve()
    if not cert.is_file() or not key.is_file():raise ConfigurationError('Supply an existing TLS certificate and private key')
    run(['openssl','x509','-in',str(cert),'-noout','-checkend','604800'])
    try:ipaddress.ip_address(hostname);option='-checkip'
    except ValueError:option='-checkhost'
    matched=run(['openssl','x509','-in',str(cert),'-noout',option,hostname])
    if b'does match certificate' not in matched:raise ConfigurationError('TLS certificate does not cover the configured hostname')
    public_cert=run(['openssl','x509','-in',str(cert),'-pubkey','-noout'])
    public_key=run(['openssl','pkey','-in',str(key),'-pubout','-passin','pass:'])
    if public_cert.strip()!=public_key.strip():raise ConfigurationError('TLS certificate does not match its private key')
    return cert.read_bytes(),key.read_bytes()

def configure(root,origin,bind,networks,cert,key,validate_tls=True,backup_directory=None,ca_file=None,project_name='toshkent-invest-portal'):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}',project_name):raise ConfigurationError('Invalid Compose project name')
    root=Path(root).resolve();directory=root/'.deploy'
    backups=Path(backup_directory).expanduser() if backup_directory else root.parent/'portal-backups'
    if not backups.is_absolute() or backups.resolve().is_relative_to(root):raise ConfigurationError('Use an absolute backup directory outside the release checkout')
    origin,hostname,port=validate_origin(origin);bind=validate_bind(bind);networks=validate_networks(networks)
    if (directory/'portal.env').exists():raise ConfigurationError('Configuration already exists; secrets were preserved. Use the existing installation.')
    cert_bytes,key_bytes=validate_certificate(cert,key,hostname) if validate_tls else (Path(cert).read_bytes(),Path(key).read_bytes())
    images=json.loads((root/'deploy/images.lock.json').read_text())
    required={'python','postgres','nginx','clamav'}
    if set(images['images'])!=required or any(not re.fullmatch(r'[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}',image) for image in images['images'].values()):
        raise ConfigurationError('Image lock must contain all four immutable references')
    installation_id=secrets.token_hex(16)
    family=validate_postgres_lock(images)
    values={'PORTAL_INSTALLATION_ID':installation_id,'PORTAL_IMAGE_PREFIX':project_name,'PORTAL_RELEASE':(root/'RELEASE').read_text().strip(),'COMPOSE_PROJECT_NAME':project_name,'APP_ENV':'production','APP_ORIGIN':origin,'ALLOWED_HOSTS':hostname,'BIND_ADDRESS':bind,'TLS_PORT':str(port),'STORAGE_DRIVER':'filesystem','REQUIRE_MFA':'none','REQUIRE_PASSWORD_CHANGE':'false','SESSION_TTL_SECONDS':'43200','SESSION_IDLE_SECONDS':'43200','PRIVATE_STORAGE_DIR':'/var/lib/portal/files','BACKUP_RETENTION_DAYS':'14'}
    for name in ['DJANGO_SECRET_KEY','WORKER_TOKEN','EDGE_PROXY_TOKEN','POSTGRES_PASSWORD','POSTGRES_OWNER_PASSWORD','POSTGRES_ADMIN_PASSWORD']:
        values[name]=secrets.token_urlsafe(48)
    values.update({name.upper()+'_IMAGE':image for name,image in images['images'].items()})
    values['POSTGRES_STORAGE_FAMILY']=family
    temporary=root/('.deploy-stage-'+secrets.token_hex(6));temporary.mkdir(mode=0o700)
    try:
        private_write(temporary/'tls/fullchain.pem',cert_bytes,0o644)
        private_write(temporary/'tls/privkey.pem',key_bytes)
        if ca_file:private_write(temporary/'tls/ca.pem',Path(ca_file).read_bytes(),0o644)
        private_write(temporary/'keys/backup.key',secrets.token_bytes(32))
        private_write(temporary/'portal.env','\n'.join(f'{key}={value}' for key,value in values.items())+'\n')
        private_write(temporary/'installation.json',json.dumps({'format':1,'installationId':installation_id,'origin':origin,'hostname':hostname,'bind':bind,'port':port,'allowNetworks':networks,'storage':'filesystem','backupDirectory':str(backups.resolve())},indent=2)+'\n')
        template=(root/'deploy/nginx.conf.in').read_text()
        acl='\n'.join('    allow '+network+';' for network in networks)+'\n    deny all;'
        template=template.replace('@@NETWORK_ACL@@',acl).replace('@@EDGE_PROXY_TOKEN@@',values['EDGE_PROXY_TOKEN']).replace('@@SERVER_NAME@@',hostname)
        private_write(temporary/'nginx.conf',template,0o600)
        temporary.replace(directory)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return {'origin':origin,'bind':bind,'port':port,'configuration':str(directory)}
