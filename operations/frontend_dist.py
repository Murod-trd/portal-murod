"""Verify a prebuilt static frontend in Python before it is shipped or baked into an image.

The browser bundle is compiled on a developer machine or in CI. Servers never run Node,
npm or any JavaScript tooling: they only check these SHA-256 bindings and copy the files.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

PROVENANCE='BUILD-PROVENANCE.json'
SHARED_SOURCES=('package.json','package-lock.json','RELEASE')
SKIPPED_PARTS={'node_modules','.git'}


def _sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def web_sources(root):
    """Same source set as apps/web/scripts/build.mjs records, for trees without git."""
    base=Path(root)/'apps/web'
    if base.is_symlink() or not base.is_dir():raise ValueError('Missing apps/web sources')
    names=[]
    for path in base.rglob('*'):
        relative=path.relative_to(base)
        if set(relative.parts)&SKIPPED_PARTS or path.name.endswith('.tsbuildinfo'):continue
        if path.is_symlink():raise ValueError('Frontend sources must not contain symlinks')
        if path.is_file():names.append('apps/web/'+relative.as_posix())
        elif not path.is_dir():raise ValueError('Frontend sources must be regular files')
    return sorted(names)


def verify(root,source_names=None):
    """Return the frontend-dist file names after checking provenance against sources and artifacts."""
    root=Path(root);directory=root/'frontend-dist'
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Build the static frontend before packaging it')
    paths=list(directory.rglob('*'))
    if any(path.is_symlink() or not (path.is_file() or path.is_dir()) for path in paths):
        raise ValueError('Unsafe frontend artifact path')
    names=sorted(path.relative_to(directory).as_posix() for path in paths if path.is_file())
    stamp=directory/PROVENANCE
    if not stamp.is_file():raise ValueError('Missing frontend build provenance; rebuild the frontend')
    provenance=json.loads(stamp.read_text(encoding='utf-8'))
    if provenance.get('format')!=1 or provenance.get('release')!=(root/'RELEASE').read_text().strip():
        raise ValueError('Frontend build release mismatch')
    web=web_sources(root) if source_names is None else [name for name in source_names if name.startswith('apps/web/')]
    expected_sources=set(web)|set(SHARED_SOURCES)
    recorded=provenance.get('sourceSha256')
    if not isinstance(recorded,dict) or set(recorded)!=expected_sources:
        raise ValueError('Frontend provenance source list mismatch')
    for name,expected in recorded.items():
        if _sha256(root/name)!=expected:raise ValueError('Frontend source changed since build: '+name)
    artifacts=set(names)-{PROVENANCE}
    hashes=provenance.get('artifactSha256')
    if 'index.html' not in artifacts or not isinstance(hashes,dict) or set(hashes)!=artifacts:
        raise ValueError('Frontend build artifact list mismatch')
    for name,expected in hashes.items():
        if _sha256(directory/name)!=expected:raise ValueError('Frontend artifact changed since build: '+name)
    return names


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(Path(__file__).resolve().parents[1]))
    args=parser.parse_args(argv)
    try:names=verify(Path(args.root))
    except (OSError,ValueError) as error:
        print('Frontend verification failed: '+str(error),file=sys.stderr);return 1
    print(json.dumps({'frontendDist':'verified','files':len(names)}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
