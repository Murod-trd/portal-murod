"""Create an integrity manifest and a source release from committed files, without secrets/runtime data."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

try:
    from .frontend_dist import verify as verify_frontend
except ImportError:
    from frontend_dist import verify as verify_frontend


def require_clean_source(root):
    status=subprocess.check_output(['git','status','--porcelain=v1','--untracked-files=all','-z'],cwd=root)
    if status:
        raise ValueError('Commit all source changes before packaging; the working tree must be clean')


def frontend_files(root, source_names):
    """Include only a previously built tree matching this exact frontend source."""
    if any(name.startswith('frontend-dist/') for name in source_names):
        raise ValueError('Frontend outputs must remain untracked build artifacts')
    return ['frontend-dist/'+name for name in verify_frontend(root,source_names)]


def prepare(root,include_frontend=False):
    root=Path(root).resolve()
    require_clean_source(root)
    names=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().strip('\0').split('\0')
    blocked={'.deploy','.runtime','.venv','node_modules','.git','__pycache__','release-artifacts'}
    names=[name for name in names if name and name!='RELEASE-MANIFEST.json']
    if any(set(Path(name).parts)&blocked or any(part.startswith('.deploy') for part in Path(name).parts) or Path(name).is_absolute() or '..' in Path(name).parts for name in names):raise ValueError('Runtime/unsafe path found in tracked source')
    if any((root/name).is_symlink() or not (root/name).is_file() for name in names):
        raise ValueError('Only regular source files may be packaged')
    if include_frontend:names+=frontend_files(root,names)
    hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
    manifest={'format':1,'release':(root/'RELEASE').read_text().strip(),'sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip(),'sha256':hashes}
    require_clean_source(root)
    (root/'RELEASE-MANIFEST.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    return names,manifest


def archive_entry(name):
    """Explicit modes: ZipFile.writestr() with a plain name stores 0600, and after `unzip` the
    unprivileged users inside the containers could not read the release files."""
    entry=zipfile.ZipInfo('portal/'+name,date_time=time.localtime()[:6])
    entry.compress_type=zipfile.ZIP_DEFLATED
    entry.create_system=3  # Unix: `unzip` applies the stored mode
    entry.external_attr=(0o100000|(0o755 if name=='portalctl' or name.endswith('.sh') else 0o644))<<16
    return entry


def write_archive(root,names,manifest,target):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.portal-release-',suffix='.zip',delete=False) as temporary:
        staged=Path(temporary.name)
    try:
        with zipfile.ZipFile(staged,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                data=(root/name).read_bytes()
                if hashlib.sha256(data).hexdigest()!=manifest['sha256'][name]:
                    raise ValueError('Source changed during packaging: '+name)
                archive.writestr(archive_entry(name),data)
            archive.writestr(archive_entry('RELEASE-MANIFEST.json'),json.dumps(manifest,sort_keys=True,indent=2)+'\n')
        with zipfile.ZipFile(staged) as archive:
            if archive.testzip():raise ValueError('Release ZIP validation failed')
        require_clean_source(root)
        os.replace(staged,target)
    finally:
        staged.unlink(missing_ok=True)


def write_checksum(target):
    target=Path(target)
    if any(character in target.name for character in ['\n','\r','\\']):
        raise ValueError('Unsupported checksum filename')
    digest=hashlib.sha256()
    with target.open('rb') as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.portal-sha256-',delete=False) as temporary:
        staged=Path(temporary.name)
    try:
        staged.write_text(digest.hexdigest()+'  '+target.name+'\n',encoding='utf-8',newline='\n')
        os.replace(staged,target.with_name(target.name+'.sha256'))
    finally:
        staged.unlink(missing_ok=True)
    return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default=str(Path(__file__).resolve().parents[1]));parser.add_argument('--output');parser.add_argument('--manifest-only',action='store_true');parser.add_argument('--include-frontend',action='store_true');args=parser.parse_args()
    root=Path(args.root).resolve();names,manifest=prepare(root,args.include_frontend)
    result={'release':manifest['release'],'sourceCommit':manifest['sourceCommit'],'files':len(names)+1}
    if not args.manifest_only:
        if not args.output:raise ValueError('--output is required')
        write_archive(root,names,manifest,args.output)
        result['archiveSha256']=write_checksum(args.output)
    print(json.dumps(result))

if __name__=='__main__':main()
