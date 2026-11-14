#!/usr/bin/env python3
"""Creates local demo secrets only. No hard-coded or browser-bundled passwords."""
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
runtime=Path(os.environ.get('PORTAL_RUNTIME_DIR',str(root/'.runtime')))
runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
config=runtime/'config.json'
if not config.exists():
    config.write_text(json.dumps({'APP_ENV':'demo','DJANGO_SECRET_KEY':secrets.token_urlsafe(48),'WORKER_TOKEN':secrets.token_urlsafe(48),'APP_ORIGIN':'http://localhost:3000','REQUIRE_MFA':'none','REQUIRE_PASSWORD_CHANGE':'false','SESSION_TTL_SECONDS':'43200','SESSION_IDLE_SECONDS':'43200','DB_ENGINE':'sqlite'},indent=2))
    config.chmod(0o600)
env={**os.environ,'DJANGO_SETTINGS_MODULE':'config.settings','PYTHONPATH':str(root/'backend')}
subprocess.run([sys.executable,str(root/'backend/manage.py'),'migrate','--noinput'],env=env,cwd=root,check=True)
if '--no-seed' not in sys.argv:
    subprocess.run([sys.executable,str(root/'backend/manage.py'),'seed_demo'],env=env,cwd=root,check=True)
print('Local setup complete. Credentials, if created, are in .runtime/demo-credentials.json (not in the frontend or source archive).')
