"""Real temporary Git/ZIP checks for the host-side release tool (stdlib only).

Run with python -m unittest operations.test_package_release. Git is needed only
on the release-authoring host, not on an installation/acceptance server.
"""
import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from operations.frontend_dist import verify as verify_frontend
from operations.package_release import prepare,write_archive,write_checksum


class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        self.work=tempfile.TemporaryDirectory(prefix='portal-package-test-')
        self.addCleanup(self.work.cleanup)
        self.root=Path(self.work.name)/'source';self.root.mkdir()
        self.git('init','-b','test')
        self.git('config','core.autocrlf','false')
        (self.root/'.gitignore').write_text('RELEASE-MANIFEST.json\n.runtime/\n')
        (self.root/'RELEASE').write_text('synthetic-test\n')
        (self.root/'source.py').write_text('value = 1\n')
        self.git('add','.')
        self.git('-c','user.name=Test','-c','user.email=test@local.invalid','commit','-m','Synthetic source')

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.STDOUT)

    def test_clean_archive_matches_commit_hashes_and_omits_ignored_secrets(self):
        private=self.root/'.runtime';private.mkdir();(private/'secret').write_text('not-for-release')
        names,manifest=prepare(self.root)
        target=Path(self.work.name)/'release.zip'
        write_archive(self.root,names,manifest,target)
        self.assertEqual(manifest['sourceCommit'],self.git('rev-parse','HEAD').decode().strip())
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(set(archive.namelist()),{'portal/'+name for name in names}|{'portal/RELEASE-MANIFEST.json'})
            self.assertEqual(json.loads(archive.read('portal/RELEASE-MANIFEST.json')),manifest)
            for entry in archive.infolist():  # container users read these files after `unzip`
                self.assertEqual((entry.create_system,(entry.external_attr>>16)&0o444),(3,0o444))
            for name,expected in manifest['sha256'].items():
                self.assertEqual(hashlib.sha256(archive.read('portal/'+name)).hexdigest(),expected)

    def test_dirty_tracked_source_is_rejected_without_replacing_manifest(self):
        manifest=self.root/'RELEASE-MANIFEST.json';manifest.write_bytes(b'previous manifest')
        (self.root/'source.py').write_text('value = 2\n')
        with self.assertRaisesRegex(ValueError,'working tree must be clean'):prepare(self.root)
        self.assertEqual(manifest.read_bytes(),b'previous manifest')

    def test_staged_source_is_rejected(self):
        (self.root/'source.py').write_text('value = 2\n');self.git('add','source.py')
        with self.assertRaisesRegex(ValueError,'working tree must be clean'):prepare(self.root)

    def test_untracked_source_is_rejected(self):
        (self.root/'missing_from_commit.py').write_text('value = 2\n')
        with self.assertRaisesRegex(ValueError,'working tree must be clean'):prepare(self.root)

    def test_post_manifest_change_preserves_existing_deliverable(self):
        names,manifest=prepare(self.root)
        target=Path(self.work.name)/'release.zip';target.write_bytes(b'previous deliverable')
        (self.root/'source.py').write_text('value = 2\n')
        with self.assertRaisesRegex(ValueError,'Source changed during packaging'):write_archive(self.root,names,manifest,target)
        self.assertEqual(target.read_bytes(),b'previous deliverable')
        self.assertEqual(list(target.parent.glob('.portal-release-*')),[])

    def frontend_fixture(self):
        (self.root/'.gitignore').write_text('RELEASE-MANIFEST.json\n.runtime/\nfrontend-dist/\n')
        sources={'apps/web/src/main.tsx':'synthetic source','package.json':'{}',
                 'package-lock.json':'{}','apps/web/scripts/build.mjs':'// synthetic build script'}
        for name,content in sources.items():
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
        self.git('add','.');self.git('-c','user.name=Test','-c','user.email=test@local.invalid','commit','-m','Synthetic frontend fixture')
        frontend=self.root/'frontend-dist';frontend.mkdir();(frontend/'index.html').write_bytes(b'<p>Synthetic artifact</p>')
        source_hashes={name:hashlib.sha256((self.root/name).read_bytes()).hexdigest() for name in [*sources,'RELEASE']}
        provenance={'format':1,'release':'synthetic-test','sourceSha256':source_hashes,
                    'artifactSha256':{'index.html':hashlib.sha256((frontend/'index.html').read_bytes()).hexdigest()}}
        (frontend/'BUILD-PROVENANCE.json').write_text(json.dumps(provenance))
        return frontend

    def test_frontend_archive_includes_exact_artifacts_and_checksum(self):
        frontend=self.frontend_fixture();names,manifest=prepare(self.root,include_frontend=True)
        target=Path(self.work.name)/'frontend.zip';write_archive(self.root,names,manifest,target)
        checksum=write_checksum(target)
        self.assertEqual(checksum,hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(target.with_name(target.name+'.sha256').read_text(),checksum+'  frontend.zip\n')
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.read('portal/frontend-dist/index.html'),(frontend/'index.html').read_bytes())

    def test_frontend_rejects_modified_asset(self):
        frontend=self.frontend_fixture();(frontend/'index.html').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'artifact changed'):prepare(self.root,include_frontend=True)

    def test_frontend_rejects_unrecorded_asset(self):
        frontend=self.frontend_fixture();(frontend/'extra.js').write_text('unexpected')
        with self.assertRaisesRegex(ValueError,'artifact list mismatch'):prepare(self.root,include_frontend=True)

    def test_frontend_rejects_stale_build_after_clean_source_commit(self):
        self.frontend_fixture();(self.root/'apps/web/src/main.tsx').write_text('changed source')
        self.git('add','.');self.git('-c','user.name=Test','-c','user.email=test@local.invalid','commit','-m','Changed synthetic source')
        with self.assertRaisesRegex(ValueError,'source changed since build'):prepare(self.root,include_frontend=True)

    def test_frontend_verifier_matches_without_git(self):
        self.frontend_fixture()
        self.assertEqual(verify_frontend(self.root),['BUILD-PROVENANCE.json','index.html'])
        (self.root/'apps/web/src/extra.ts').write_text('unbuilt source')
        with self.assertRaisesRegex(ValueError,'source list mismatch'):verify_frontend(self.root)

    def test_frontend_rejects_missing_provenance(self):
        frontend=self.frontend_fixture();(frontend/'BUILD-PROVENANCE.json').unlink()
        with self.assertRaisesRegex(ValueError,'Missing frontend build provenance'):prepare(self.root,include_frontend=True)


if __name__=='__main__':unittest.main()
