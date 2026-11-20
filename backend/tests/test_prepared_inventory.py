import json
import subprocess

import pytest

from operations.security_scan import inspect_prepared_platform


def inventory_root(root):
    (root/'deploy/postgres').mkdir(parents=True)
    packages={'su-exec':'0.3-r0','postgresql17':'17.11-r0','postgresql17-client':'17.11-r0','postgresql17-contrib':'17.11-r0'}
    (root/'deploy/images.lock.json').write_text(json.dumps({'postgresPackages':packages}))
    (root/'deploy/postgres/UPSTREAM.json').write_text(json.dumps({'patchedSha256':'a'*64}))
    return packages


def test_postgres_inventory_queries_installed_packages_without_network(monkeypatch,tmp_path):
    packages=inventory_root(tmp_path)
    calls=[]
    def output(command,**kwargs):
        calls.append(command)
        if command[command.index('--entrypoint')+1]=='sh':
            return '/sbin/su-exec\npostgres (PostgreSQL) 17.11\n'+'a'*64+'  /usr/local/bin/docker-entrypoint.sh\n'
        return json.dumps([{'name':name,'version':version} for name,version in packages.items()])
    monkeypatch.setattr(subprocess,'check_output',output)
    result=inspect_prepared_platform('isolated-candidate','postgres',tmp_path)
    assert result['passed'] is True
    assert '--no-network' in calls[1] and 'installed' in calls[1]


@pytest.mark.parametrize('output',['[]','{}','not json'])
def test_inventory_does_not_approve_missing_package(monkeypatch,tmp_path,output):
    inventory_root(tmp_path)
    def call(command,**kwargs):
        if command[command.index('--entrypoint')+1]=='sh':
            return '/sbin/su-exec\npostgres (PostgreSQL) 17.11\n'+'a'*64+'  entrypoint\n'
        return output
    monkeypatch.setattr(subprocess,'check_output',call)
    assert inspect_prepared_platform('isolated-candidate','postgres',tmp_path)['passed'] is False


def test_inventory_refuses_any_platform_other_than_postgres(monkeypatch,tmp_path):
    inventory_root(tmp_path)
    monkeypatch.setattr(subprocess,'check_output',lambda *args,**kwargs:pytest.fail('nothing may run'))
    assert inspect_prepared_platform('isolated-compiler','frontend-tools',tmp_path)=={'passed':False,'errorType':'ValueError'}
