"""The editor must be able to round-trip and explicitly clear publication dates."""
from datetime import timedelta

import pytest
from django.utils import timezone
from portal import models as m, services


@pytest.mark.django_db
@pytest.mark.parametrize('category', ['NEWS', 'COMPANY'])
def test_company_category_includes_legacy_news_without_exposing_drafts(hr, employee, category):
    originals = [m.News.objects.create(title=value, category=value, body='Synthetic',
                 author=hr[0], status='PUBLISHED') for value in ['NEWS', 'COMPANY']]
    m.News.objects.create(title='Other', category='TEAM', body='Synthetic', author=hr[0], status='PUBLISHED')
    m.News.objects.create(title='Private draft', category='NEWS', body='Synthetic', author=hr[0], status='DRAFT')
    result = services.news(employee[0], {'category': category})
    assert {item['id'] for item in result['items']} == {str(item.id) for item in originals}
    assert list(m.News.objects.filter(id__in=[item.id for item in originals]).order_by('category').values_list('category', flat=True)) == ['COMPANY', 'NEWS']


@pytest.mark.django_db
def test_news_schedule_read_and_clear(hr):
    _, _, api = hr
    start = timezone.now() + timedelta(days=1)
    end = start + timedelta(days=2)
    created = api.post('/api/v1/news', {
        'title': 'Synthetic scheduled news', 'body': '<p>Schedule</p>',
        'status': 'DRAFT', 'publishedAt': start.isoformat(), 'expiresAt': end.isoformat(),
    })
    assert created.status_code == 201, created.content
    path = '/api/v1/news/' + created.json()['id']
    detail = api.get(path).json()
    assert detail['publishedAt'] == start.isoformat()
    assert detail['expiresAt'] == end.isoformat()
    changed = api.patch(path, {'publishedAt': None, 'expiresAt': None})
    assert changed.status_code == 200, changed.content
    detail = api.get(path).json()
    assert detail['publishedAt'] is None
    assert detail['expiresAt'] is None


@pytest.mark.django_db
def test_scheduled_news_is_listed_for_managers_flagged_and_hidden_from_employees(hr, employee):
    soon = timezone.now() + timedelta(days=2)
    scheduled = m.News.objects.create(title='Scheduled item', body='x', author=hr[0], status='PUBLISHED', published_at=soon)
    live = m.News.objects.create(title='Live item', body='x', author=hr[0], status='PUBLISHED', published_at=timezone.now() - timedelta(hours=1))
    m.News.objects.create(title='Draft item', body='x', author=hr[0], status='DRAFT')
    m.News.objects.create(title='Expired item', body='x', author=hr[0], status='PUBLISHED', expires_at=timezone.now() - timedelta(hours=1))
    managed = {item['title']: item for item in services.news(hr[0], {})['items']}
    assert set(managed) == {'Scheduled item', 'Live item'}  # drafts and expired items still need the manage filter
    assert managed['Scheduled item']['scheduled'] is True and managed['Live item']['scheduled'] is False
    assert managed['Scheduled item']['publishedAt'] == soon.isoformat()
    assert {item['title'] for item in services.news(hr[0], {'manage': 'true'})['items']} == {'Scheduled item', 'Live item', 'Draft item', 'Expired item'}
    assert [item['title'] for item in services.news(employee[0], {})['items']] == ['Live item']
    assert [item['title'] for item in services.news(employee[0], {'manage': 'true'})['items']] == ['Live item']
    detail = hr[2].get(f'/api/v1/news/{scheduled.id}').json()
    assert detail['scheduled'] is True and detail['expired'] is False
    assert employee[2].get(f'/api/v1/news/{scheduled.id}').status_code == 404
    assert employee[2].post(f'/api/v1/news/{scheduled.id}/view', {}).status_code == 404  # the UI must not send this ping for hidden items
    assert hr[2].get(f'/api/v1/news/{live.id}').json()['scheduled'] is False
