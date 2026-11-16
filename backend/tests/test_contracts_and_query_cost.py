import re
import ast
from pathlib import Path
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from portal import models as m, services as svc
from portal.contracts import RESPONSES, BINARY_VIEWS

pytestmark = pytest.mark.django_db


def test_every_public_view_method_has_explicit_response_contract():
    root = Path(__file__).resolve().parents[1]/'portal'
    for filename in ['views.py', 'auth_views.py', 'file_views.py']:
        for view in ast.parse((root/filename).read_text()).body:
            if not isinstance(view, ast.ClassDef) or view.name.startswith('Worker'):
                continue
            for method in view.body:
                if isinstance(method, ast.FunctionDef) and method.name in {'get', 'post', 'patch', 'put', 'delete'}:
                    assert view.name in BINARY_VIEWS or method.name in RESPONSES.get(view.name, {})


def test_news_listing_query_count_is_constant_for_many_articles(employee, hr):
    for index in range(25):
        m.News.objects.create(title=f'Synthetic news {index}', body='text', author=hr[0], status='PUBLISHED', published_at=timezone.now())
    with CaptureQueriesContext(connection) as captured:
        result = svc.news(employee[0], {'limit': 25})
    assert len(result['items']) == 25
    assert len(captured) == 2


def test_document_lists_fetch_bounded_snippets_without_complete_text(hr, employee):
    from .test_content_and_files import create_doc
    document, _, version = create_doc(hr, new_text='searchword '+('long text '*100000))
    with CaptureQueriesContext(connection) as captured:
        result = svc.documents(employee[0], {'q': 'searchword', 'inside': 'true'})
    assert result['total'] == 1
    assert result['items'][0]['snippet'].startswith('searchword')
    assert len(result['items'][0]['snippet']) <= 360
    assert len(captured) == 2
    assert 'SUBSTR' in captured[0]['sql'].upper()
    selected = captured[0]['sql'].split(' FROM ')[0]
    for column in ('normalized_text', 'extracted_text'):  # only ever used inside SUBSTR/INSTR/LENGTH, never selected whole
        assert not re.findall(r'(?<!\()"portal_documentversion"\."%s"' % column, selected)
    history = svc.documents(employee[0], {'q': 'searchword', 'inside': 'true', 'history': 'true'})
    assert history['items'][0]['snippet'].startswith('searchword')


def test_browser_types_match_public_schema():
    from django.core.management import call_command
    call_command('generate_api_types', check=True)


@pytest.mark.django_db(transaction=True)
def test_ocr_migration_preserves_immutable_versions_forwards_and_backwards(hr):
    from django.db import IntegrityError, transaction
    from django.db.migrations.executor import MigrationExecutor
    from .test_content_and_files import create_doc
    document, old, latest = create_doc(hr)
    def assert_immutable():
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('UPDATE portal_documentversion SET label=%s WHERE id=%s', ['replacement', old.pk.hex if connection.vendor == 'sqlite' else old.pk])
    try:
        MigrationExecutor(connection).migrate([('portal', '0004_fold_document_search')])
        assert_immutable()
        MigrationExecutor(connection).migrate([('portal', '0006_document_search_indexes')])
        assert_immutable()
    finally:
        MigrationExecutor(connection).migrate([('portal', '0006_document_search_indexes')])


def snippet_doc(hr, text, **kwargs):
    from .test_content_and_files import create_doc
    document, _, version = create_doc(hr, new_text=text, **kwargs)
    return document, version


@pytest.mark.parametrize('history', ['', 'true'])
def test_inside_search_returns_text_around_the_match_with_its_position(hr, employee, history):
    text = ('Lorem ipsum dolor sit amet. ' * 4000) + 'Tekshirish natijalari shu yerda keltirilgan. ' + ('tail words here ' * 3000)
    snippet_doc(hr, text)
    with CaptureQueriesContext(connection) as captured:
        result = svc.documents(employee[0], {'q': 'natijalari', 'inside': 'true', **({'history': 'true'} if history else {})})
    item = result['items'][0]
    start, end = item['snippetMatch']
    assert item['snippet'][start:end] == 'natijalari'
    assert 'Tekshirish' in item['snippet'] and 'Lorem ipsum' in item['snippet'] and len(item['snippet']) <= 360
    assert item['snippet'].startswith('…') and item['snippet'].endswith('…')
    assert len(captured) == 2  # no per-row follow-up query for a located match


def test_inside_search_locates_folded_matches_in_the_original_wording(hr, employee):
    # sh/ch/o'/g' shorten the folded text, so the folded offset drifts away from the original offset.
    filler = "Shahar choyxona o'quvchi g'alaba shirin chiroyli " * 3000
    snippet_doc(hr, filler + "Yakuniy O'zbekiston Respublikasi qarori " + filler[:4000])
    for query, shown in [("O'ZBEKISTON", "O'zbekiston"), ('ozbekiston respublikasi', "O'zbekiston Respublikasi"), ('qarori', 'qarori')]:
        item = svc.documents(employee[0], {'q': query, 'inside': 'true'})['items'][0]
        start, end = item['snippetMatch']
        assert item['snippet'][start:end].lower().replace('’', "'") == shown.lower(), (query, item['snippet'])


def test_inside_search_falls_back_to_the_head_when_only_the_title_matches(hr, employee):
    snippet_doc(hr, 'opening words ' + 'filler ' * 500)
    item = svc.documents(employee[0], {'q': 'synthetic', 'inside': 'true'})['items'][0]
    assert item['snippet'].startswith('opening words') and 'snippetMatch' not in item


def test_inside_search_snippet_is_plain_text_even_for_markup(hr, employee):
    snippet_doc(hr, 'x ' * 300 + '<script>alert(1)</script> needle <b>bold</b>')
    item = svc.documents(employee[0], {'q': 'needle', 'inside': 'true'})['items'][0]
    start, end = item['snippetMatch']
    assert item['snippet'][start:end] == 'needle' and '<script>' in item['snippet']  # returned verbatim, rendered as text by the UI


def test_inside_search_never_reveals_text_of_versions_an_employee_cannot_see(hr, employee):
    document, current = snippet_doc(hr, 'public wording')
    old = document.versions.exclude(pk=current.pk).get()
    history = {'q': 'archival', 'inside': 'true', 'history': 'true'}
    assert svc.documents(employee[0], history)['items'][0]['snippet'] == 'archival needle'
    m.FileObject.objects.filter(pk=old.file_id).update(security_status='QUARANTINED')
    assert svc.documents(employee[0], history)['total'] == 0  # an unapproved version contributes no snippet
    m.FileObject.objects.filter(pk=old.file_id).update(security_status='APPROVED')
    m.Document.objects.filter(pk=document.pk).update(status='DRAFT')
    assert svc.documents(employee[0], history)['total'] == 0
    assert svc.documents(employee[0], {'q': 'public', 'inside': 'true'})['total'] == 0
    assert svc.search(employee[0], 'public')['documents']['total'] == 0
    assert svc.documents(hr[0], {**history, 'manage': 'true'})['items'][0]['snippet'] == 'archival needle'
