"""Every error code a browser user can receive has Uzbek, Russian and English text, so no language falls back to a generic message."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = re.compile(r'[A-Z][A-Z0-9_]+')
# Raised or returned by the server (and by the file worker, which reports them through file state) in a user-visible place.
CALLS = {'PortalError', 'ProcessingError', 'OcrFailure'}
# Codes that are assigned rather than raised, and codes the SPA itself produces.
ASSIGNED = {'WORKER_RETRY_LIMIT', 'FILE_MISSING'}
CLIENT = {'NETWORK_ERROR', 'REQUEST_FAILED', 'INVALID_RESPONSE'}
# Machine-to-machine or deployment-only answers that a signed-in browser user never sees.
INTERNAL = {'WORKER_AUTH_REQUIRED', 'LEASE_EXPIRED', 'INVALID_WORKER_CAPABILITIES', 'INVALID_RESULT', 'INVALID_EXTRACTION_RESULT',
            'INVALID_IMPORT_RESULT', 'INVALID_DERIVATIVE', 'INCOMPLETE_SCAN_RESULT', 'INVALID_PROXY_HEADER', 'FRONTEND_NOT_BUILT'}


def backend_codes():
    found = set(ASSIGNED | CLIENT)
    sources = [p for p in (ROOT/'backend'/'portal').rglob('*.py') if 'migrations' not in p.parts and 'management' not in p.parts]
    for path in sources + [ROOT/'backend'/'worker.py']:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            values = []
            if isinstance(node, ast.Call) and getattr(node.func, 'id', getattr(node.func, 'attr', '')) in CALLS and node.args:
                values.append(node.args[0])
            if isinstance(node, ast.Dict):
                values += [v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value in {'code', 'error'}]
            for value in values:
                found |= {c.value for c in ast.walk(value) if isinstance(c, ast.Constant) and isinstance(c.value, str) and CODE.fullmatch(c.value)}
    return found - INTERNAL


def i18n_codes():
    source = (ROOT/'apps'/'web'/'src'/'i18n.ts').read_text(encoding='utf-8')
    uz_ru = re.search(r'export const errors:[^=]*=\{(.*?)\n\};', source, re.S).group(1)
    english = re.search(r'const englishErrors:[^=]*=\{(.*?)\};\s*$', source, re.S).group(1)
    return set(re.findall(r'(?:^|[,{\s])([A-Z][A-Z0-9_]+):\[', uz_ru)), set(re.findall(r'(?:^|[,{\s])([A-Z][A-Z0-9_]+):"', english))


def test_uzbek_russian_and_english_error_texts_cover_the_same_codes():
    uz_ru, english = i18n_codes()
    assert uz_ru == english, {'only uz/ru': sorted(uz_ru - english), 'only en': sorted(english - uz_ru)}


def test_every_user_facing_backend_code_has_text_in_all_languages():
    uz_ru, english = i18n_codes()
    needed = backend_codes()
    assert not needed - (uz_ru & english), sorted(needed - (uz_ru & english))
    assert len(needed) > 80  # the scan itself must keep finding the codes, or this guard silently passes


def test_error_texts_have_no_dead_codes():
    uz_ru, _ = i18n_codes()
    assert not uz_ru - backend_codes() - {'DELETED'}, sorted(uz_ru - backend_codes())
