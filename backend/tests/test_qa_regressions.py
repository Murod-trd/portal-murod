import json
import uuid
import pytest
from django.conf import settings
from portal import models as m

pytestmark=pytest.mark.django_db

def raw(api,method,path,body):
    """Send an exact JSON body (the API helper turns falsy bodies into {})."""
    return getattr(api.client,method.lower())(path,data=json.dumps(body),content_type='application/json',HTTP_ORIGIN=settings.APP_ORIGIN,HTTP_X_CSRFTOKEN=api.token)

NON_OBJECT_BODIES=[[],123,None,'text',[1]]

def employee_of(org,name='Archive target',**extra):
    return m.Employee.objects.create(display_name=name,position_title='Specialist',unit=org[1],office=org[0],**extra)

# 1. non-object JSON bodies
@pytest.mark.parametrize('body',NON_OBJECT_BODIES)
def test_non_object_bodies_are_400_not_500(hr,admin,org,body):
    _,_,api=hr
    _,_,admin_api=admin
    target=employee_of(org)
    news=m.News.objects.create(title='T',body='B',author=hr[0],deleted_at='2020-01-01T00:00:00Z')
    cases=[(api,'PATCH','/api/v1/me'),(api,'POST',f'/api/v1/employees/{target.id}/archive'),(api,'POST','/api/v1/employees/import/preview'),
        (api,'POST',f'/api/v1/employees/import/{uuid.uuid4()}/commit'),(api,'POST','/api/v1/me/mfa/start'),(api,'POST','/api/v1/me/mfa/verify'),
        (api,'DELETE',f'/api/v1/trash/news/{news.id}'),(admin_api,'POST',f'/api/v1/admin/users/{hr[0].pk}/reset-mfa'),
        (api,'PATCH','/api/v1/me/contact'),(api,'POST','/api/v1/employees'),(api,'POST','/api/v1/news')]
    for client,method,path in cases:
        response=raw(client,method,path,body)
        assert response.status_code==400,(method,path,response.status_code,response.content)
        assert response.json()['code']=='VALIDATION_ERROR'
    target.refresh_from_db()
    assert target.status=='ACTIVE'

@pytest.mark.parametrize('locale',[['uz'],{'a':1},1,None,True,'de'])
def test_locale_must_be_a_known_string(hr,locale):
    _,_,api=hr
    response=raw(api,'PATCH','/api/v1/me',{'locale':locale})
    assert response.status_code==400 and response.json()['code']=='VALIDATION_ERROR'
    assert raw(api,'PATCH','/api/v1/me',{'locale':'ru'}).status_code==200

@pytest.mark.parametrize('flag',[[1],'true',1,'yes',{'a':1},None])
def test_archive_requires_strict_boolean_confirmation(hr,org,flag):
    _,_,api=hr
    target=employee_of(org)
    response=raw(api,'POST',f'/api/v1/employees/{target.id}/archive',{'confirmDetach':flag})
    assert response.status_code==400 and response.json()['code']=='VALIDATION_ERROR'
    target.refresh_from_db()
    assert target.status=='ACTIVE'
    assert raw(api,'POST',f'/api/v1/employees/{target.id}/archive',{'confirmDetach':True}).status_code==200

def test_archive_with_reports_needs_literal_true(hr,org):
    _,_,api=hr
    boss=employee_of(org,'Boss person')
    employee_of(org,'Report person',manager=boss)
    assert raw(api,'POST',f'/api/v1/employees/{boss.id}/archive',{'confirmDetach':False}).status_code==409
    assert raw(api,'POST',f'/api/v1/employees/{boss.id}/archive',{'replacementManagerId':[1]}).status_code==400

def test_import_decisions_must_be_strings(hr,org):
    from .test_imports import make_file
    _,_,api=hr
    f=make_file(hr,[['','Decision person','Specialist',org[1].name,org[0].name]])
    p=api.post('/api/v1/employees/import/preview',{'fileId':str(f.id)}).json()
    for bad in [[1],{'a':1},1,None]:
        response=raw(api,'POST',f'/api/v1/employees/import/{p["id"]}/commit',{'previewHash':p['previewHash'],'decisions':{'2':bad}})
        assert response.status_code in {400,409},(bad,response.status_code)
        assert response.status_code!=500
    assert raw(api,'POST','/api/v1/employees/import/preview',{'fileId':[1]}).status_code==400
    assert not m.Employee.objects.filter(display_name='Decision person').exists()

# 2. news dates
def news_body(**extra):
    return {'title':'Dates','body':'<p>Body</p>',**extra}

@pytest.mark.parametrize('field',['publishedAt','expiresAt'])
@pytest.mark.parametrize('value',['0001-01-01T00:00:00','9999-12-31T23:59:59','1999-12-31T23:59:59Z','2101-01-01T00:00:00Z','0001-01-01T00:00:00+14:00','9999-12-31T23:59:59-14:00'])
def test_news_dates_out_of_range_are_400(hr,field,value):
    _,_,api=hr
    response=api.post('/api/v1/news',news_body(**{field:value}))
    assert response.status_code==400 and response.json()['code']=='VALIDATION_ERROR',response.content
    assert not m.News.objects.exists()

def test_news_expiry_must_follow_publication(hr):
    _,_,api=hr
    for published,expires in [('2030-01-02T00:00:00Z','2030-01-01T00:00:00Z'),('2030-01-01T00:00:00Z','2030-01-01T00:00:00Z')]:
        assert api.post('/api/v1/news',news_body(publishedAt=published,expiresAt=expires)).status_code==400
    created=api.post('/api/v1/news',news_body(publishedAt='2030-01-01T00:00:00Z',expiresAt='2030-02-01T00:00:00Z'))
    assert created.status_code==201,created.content
    nid=created.json()['id']
    # a PATCH that sends only one side is compared with the stored other side
    assert api.patch(f'/api/v1/news/{nid}',{'expiresAt':'2029-12-31T00:00:00Z'}).status_code==400
    assert api.patch(f'/api/v1/news/{nid}',{'publishedAt':'2030-03-01T00:00:00Z'}).status_code==400
    assert api.patch(f'/api/v1/news/{nid}',{'expiresAt':'2030-06-01T00:00:00Z'}).status_code==200
    assert api.patch(f'/api/v1/news/{nid}',{'expiresAt':None}).status_code==200
