#!/usr/bin/env python3
"""One preview server: Django serves the compiled static React frontend and API."""
import os
import subprocess
import sys
import time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
os.chdir(root)
python=root/'.venv/bin/python'
if not python.exists():
    python=Path(sys.executable)
runtime=root/'.runtime'
if not (runtime/'config.json').exists():
    subprocess.run([str(python),'scripts/setup.py'],check=True)
if not (root/'frontend-dist/index.html').exists():
    raise SystemExit('Build the static frontend first on this developer machine: npm run build:web (Python never starts Node).')
port=os.environ.get('PORT','3000')
host=os.environ.get('HOST','127.0.0.1')
environment={**os.environ,'WORKER_API_URL':f'http://127.0.0.1:{port}'}
children=[]
try:
    children.append(subprocess.Popen([str(python),'backend/manage.py','runserver',f'{host}:{port}','--noreload'],env=environment))
    if sys.platform=='linux':children.append(subprocess.Popen([str(python),'backend/worker.py'],env=environment))
    else:print('File scanning/OCR worker not started: it requires Linux (use Docker or WSL). The API keeps running.',flush=True)
    while True:
        if children[0].poll() is not None:
            raise SystemExit(children[0].returncode)
        if len(children)>1 and children[1].poll() is not None:
            time.sleep(3)
            children[1]=subprocess.Popen([str(python),'backend/worker.py'],env=environment)
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    for child in children:
        child.terminate()
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
