import json
from django.db import connection
from django.test.utils import CaptureQueriesContext
from portal import services as svc
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

# 3. export phones / formula neutralisation
def export_roundtrip(hr,org,fmt):
    from portal.file_processing import parse_import
    from .test_content_and_files import approved_fixture
    user,_,api=hr
    e=employee_of(org,'Round trip person',mobile_phone='+998 90 123-45 67',extension='1234',work_email='rt@example.org')
    response=api.get('/api/v1/employees/export',{'format':fmt})
    assert response.status_code==200
    name='export.'+fmt
    parsed=parse_import(response.content,name)
    file=approved_fixture(user,name,response.content,purpose='IMPORT')
    file.parsed_data=parsed;file.save()
    return e,parsed,api.post('/api/v1/employees/import/preview',{'fileId':str(file.id)}).json()

@pytest.mark.parametrize('fmt',['csv','xlsx'])
def test_export_keeps_phones_and_reimports(hr,org,fmt):
    e,parsed,preview=export_roundtrip(hr,org,fmt)
    cells=next(r for r in parsed['rows'] if r[1]=='Round trip person')
    assert cells[parsed['headers'].index('mobilePhone')]=='+998 90 123-45 67'
    row=next(r for r in preview['rows'] if r['data']['displayName']=='Round trip person')
    assert not preview['errors'],preview['errors']
    assert row['data']['mobilePhone']=='+998 90 123-45 67' and row['existingId']==str(e.id)

def test_xlsx_export_writes_plain_strings_never_formulas(hr,org):
    import io,openpyxl
    employee_of(org,'=HYPERLINK("x")',mobile_phone='+998901234567')
    _,_,api=hr
    book=openpyxl.load_workbook(io.BytesIO(api.get('/api/v1/employees/export',{'format':'xlsx'}).content))
    cells=[c for row in book.active.iter_rows(min_row=2) for c in row if isinstance(c.value,str)]
    assert cells and all(c.data_type=='s' for c in cells)
    assert any(c.value=='+998901234567' for c in cells) and any(c.value.startswith("'=HYPERLINK") for c in cells)

def test_formula_defence_and_apostrophe_restore():
    from portal.domain import spreadsheet_safe,spreadsheet_restore
    for bad in ['=1+1','@SUM(A1)','-2+3','+1+1','+998 (90) abc','+','  =x','\t=x','+998;cmd']:
        assert spreadsheet_safe(bad).startswith("'"),bad
    for ok in ['+998901234567','+998 90 123 45 67','+7 (495) 123-45-67','plain',"o'brien"]:
        assert spreadsheet_safe(ok)==ok
    assert spreadsheet_restore("'+998 90 123 45 67")=='+998 90 123 45 67' and spreadsheet_restore("'=x")=='=x'
    assert spreadsheet_restore("'plain")=="'plain" and spreadsheet_restore('+998')=='+998'
    for value in ['=x','-a','+a','@a']:
        assert spreadsheet_restore(spreadsheet_safe(value))==value

def test_old_apostrophe_phone_exports_still_import(hr,org):
    from .test_content_and_files import approved_fixture
    user,_,api=hr
    f=approved_fixture(user,'old.csv',b'x',purpose='IMPORT')
    f.parsed_data={'headers':['displayName','positionTitle','unit','office','mobilePhone'],'rows':[['Old export','Specialist',org[1].name,org[0].name,"'+998 90 123 45 67"]]};f.save()
    p=api.post('/api/v1/employees/import/preview',{'fileId':str(f.id)}).json()
    assert not p['errors'] and p['rows'][0]['data']['mobilePhone']=='+998 90 123 45 67'

def emp_body(org,**extra):
    return {'displayName':'Fresh Person','positionTitle':'Specialist','unitId':str(org[1].id),'officeId':str(org[0].id),**extra}

# 4. restore into inactive unit / office, 10. repeated archive / restore
def test_restore_into_inactive_unit_or_office_is_409(hr,org):
    _,_,api=hr
    e=employee_of(org,status='ARCHIVED')
    org[1].active=False;org[1].save()
    response=api.post(f'/api/v1/employees/{e.id}/restore')
    assert response.status_code==409 and response.json()['code']=='UNIT_INACTIVE'
    org[1].active=True;org[1].save();org[0].active=False;org[0].save()
    response=api.post(f'/api/v1/employees/{e.id}/restore')
    assert response.status_code==409 and response.json()['code']=='OFFICE_INACTIVE'
    e.refresh_from_db()
    assert e.status=='ARCHIVED'
    org[0].active=True;org[0].save()
    assert api.post(f'/api/v1/employees/{e.id}/restore').status_code==200

def test_repeated_archive_and_restore_are_409_and_keep_state(hr,org):
    _,_,api=hr
    e=employee_of(org)
    assert api.post(f'/api/v1/employees/{e.id}/restore').status_code==409
    assert api.post(f'/api/v1/employees/{e.id}/archive').status_code==200
    e.refresh_from_db();stamp=e.archived_at
    assert stamp
    again=api.post(f'/api/v1/employees/{e.id}/archive')
    assert again.status_code==409 and again.json()['code']=='CONFLICT'
    e.refresh_from_db()
    assert e.archived_at==stamp
    assert api.post(f'/api/v1/employees/{e.id}/restore').status_code==200
    assert api.post(f'/api/v1/employees/{e.id}/restore').status_code==409

# 5. namesake confirmation
def test_namesake_asked_only_on_create_or_rename_and_ignores_archived(hr,org):
    _,_,api=hr
    employee_of(org,'Fresh Person')
    assert api.post('/api/v1/employees',emp_body(org)).status_code==409
    created=api.post('/api/v1/employees',emp_body(org,confirmNamesake=True))
    assert created.status_code==201
    eid=created.json()['id']
    # unrelated edit and an unchanged name do not need confirmation
    assert api.patch(f'/api/v1/employees/{eid}',{'floor':'3'}).status_code==200
    assert api.patch(f'/api/v1/employees/{eid}',{'displayName':'Fresh Person','room':'7'}).status_code==200
    # a real rename onto an existing name does
    employee_of(org,'Other Name')
    assert api.patch(f'/api/v1/employees/{eid}',{'displayName':'Other Name'}).status_code==409
    assert api.patch(f'/api/v1/employees/{eid}',{'displayName':'Other Name','confirmNamesake':True}).status_code==200
    # archived namesakes do not count
    employee_of(org,'Gone Person',status='ARCHIVED')
    assert api.post('/api/v1/employees',emp_body(org,displayName='Gone Person')).status_code==201
    assert [x['status'] for x in api.get('/api/v1/employees/duplicates',{'name':'Gone Person'}).json()]==['ACTIVE']

# 6. org-unit / office duplicates
def test_unit_duplicate_names_are_rejected(hr,org):
    _,_,api=hr
    response=api.post('/api/v1/org-units',{'name':'  TEST   department '})
    assert response.status_code==409 and response.json()['code']=='UNIT_ALREADY_EXISTS'
    parent=api.post('/api/v1/org-units',{'name':'Parent unit'}).json()['id']
    assert api.post('/api/v1/org-units',{'name':'test department','parentId':parent}).status_code==409
    other=api.post('/api/v1/org-units',{'name':'Other unit'}).json()['id']
    assert api.patch(f'/api/v1/org-units/{other}',{'name':'Test Department'}).status_code==409
    assert api.patch(f'/api/v1/org-units/{other}',{'name':'Other unit','type':'DIVISION'}).status_code==200

def test_inactive_unit_name_can_be_reused_but_not_reactivated_twice(hr,org):
    _,_,api=hr
    old=m.OrgUnit.objects.create(name='Legacy unit',active=False)
    assert api.post('/api/v1/org-units',{'name':'Legacy unit'}).status_code==201
    assert api.patch(f'/api/v1/org-units/{old.id}',{'active':True}).status_code==409

def test_unit_resolution_in_import_stays_unambiguous(hr,org):
    from .test_imports import make_file
    _,_,api=hr
    assert api.post('/api/v1/org-units',{'name':org[1].name}).status_code==409
    f=make_file(hr,[['','Unambiguous','Specialist',org[1].name,org[0].name]])
    assert not api.post('/api/v1/employees/import/preview',{'fileId':str(f.id)}).json()['errors']

def test_office_duplicate_checked_on_reactivation_without_name(hr,org):
    _,_,api=hr
    old=m.Office.objects.create(name='Test office',active=False)
    assert api.patch(f'/api/v1/offices/{old.id}',{'active':True}).status_code==409
    assert api.post('/api/v1/offices',{'name':'test OFFICE'}).status_code==409
    spare=m.Office.objects.create(name='Spare office',active=False)
    assert api.patch(f'/api/v1/offices/{spare.id}',{'active':True}).status_code==200

def test_unit_cannot_be_reactivated_under_inactive_parent(hr,org):
    _,_,api=hr
    parent=m.OrgUnit.objects.create(name='Closed parent',active=False)
    child=m.OrgUnit.objects.create(name='Closed child',parent=parent,active=False)
    response=api.patch(f'/api/v1/org-units/{child.id}',{'active':True})
    assert response.status_code==409 and response.json()['code']=='UNIT_INACTIVE'
    parent.active=True;parent.save()
    assert api.patch(f'/api/v1/org-units/{child.id}',{'active':True}).status_code==200

# 7. real spreadsheet row numbers
def test_parse_import_keeps_real_row_numbers_with_blank_rows():
    import io,openpyxl
    from portal.file_processing import parse_import
    csv_data=b'name,x\nfirst,1\n\n\nsecond,2\n,\nthird,3\n'
    parsed=parse_import(csv_data,'a.csv')
    assert [r[0] for r in parsed['rows']]==['first','second','third'] and parsed['rowNumbers']==[2,5,7]
    book=openpyxl.Workbook();sheet=book.active
    sheet.append(['name','x']);sheet.append(['first',1]);sheet.append([]);sheet.append(['second',2]);sheet.cell(row=7,column=1,value='third')
    out=io.BytesIO();book.save(out)
    parsed=parse_import(out.getvalue(),'a.xlsx')
    assert [r[0] for r in parsed['rows']]==['first','second','third'] and parsed['rowNumbers']==[2,4,7]

def test_preview_and_commit_use_real_row_numbers(hr,org):
    from .test_imports import make_file
    _,_,api=hr
    ok=lambda name:['',name,'Specialist',org[1].name,org[0].name]
    f=make_file(hr,[ok('Row two'),['','Row five','Specialist','missing unit',org[0].name],ok('Row six')])
    f.parsed_data['rowNumbers']=[2,5,6];f.save()
    p=api.post('/api/v1/employees/import/preview',{'fileId':str(f.id)}).json()
    assert [r['row'] for r in p['rows']]==[2,5,6]
    assert [e['row'] for e in p['errors']]==[5]
    f.parsed_data['rows'][1][3]=org[1].name;f.save()
    p=api.post('/api/v1/employees/import/preview',{'fileId':str(f.id)}).json()
    assert not p['errors']
    ser=api.post(f'/api/v1/employees/import/{p["id"]}/commit',{'previewHash':p['previewHash'],'decisions':{'5':'SKIP'}})
    assert ser.status_code==200 and ser.json()['skipped']==1 and ser.json()['created']==2
    assert not m.Employee.objects.filter(display_name='Row five').exists()
    assert [i['row'] for i in ser.json()['items']]==[2,6]

def test_stored_parsed_data_without_row_numbers_still_previews(hr,org):
    from .test_imports import make_file
    _,_,api=hr
    f=make_file(hr,[['','Legacy one','Specialist',org[1].name,org[0].name],['','Legacy two','Specialist',org[1].name,org[0].name]])
    assert 'rowNumbers' not in f.parsed_data
    assert [r['row'] for r in api.post('/api/v1/employees/import/preview',{'fileId':str(f.id)}).json()['rows']]==[2,3]

def test_worker_result_stores_and_validates_row_numbers(hr):
    from django.conf import settings
    from .test_content_and_files import approved_fixture
    user,_,api=hr
    for numbers,expected in [([2,5],200),([2],409),(['2',5],409),([5,2],409),([1,2],409),('x',409),(None,200)]:
        f=approved_fixture(user,'import.csv',b'header\nrow',purpose='IMPORT')
        m.ProcessingJob.objects.filter(status__in=['PENDING','RUNNING']).delete()
        m.ProcessingJob.objects.update_or_create(file=f,kind='PARSE_IMPORT',defaults={'status':'PENDING','attempts':0})
        claim=api.client.post('/api/internal/worker/claim',{},content_type='application/json',HTTP_AUTHORIZATION='Bearer '+settings.WORKER_TOKEN)
        job=claim.json()['job']
        body={'headers':['a'],'rows':[['1'],['2']]}
        if numbers is not None:body['rowNumbers']=numbers
        response=api.client.post(f'/api/internal/worker/{job["id"]}/result',body,content_type='application/json',HTTP_AUTHORIZATION='Bearer '+job['lease'])
        assert response.status_code in {200,400,409},response.content
        f.refresh_from_db()
        if numbers in ([2,5],None) :
            assert response.status_code==200 and f.parsed_data.get('rowNumbers')==numbers
        else:
            assert response.status_code!=200 and f.parsed_data is None

# 8. login throttle counts failures only
def test_successful_logins_do_not_consume_the_username_budget(hr):
    from .conftest import API,TEST_PASSWORD
    name=hr[0].username
    attempt=lambda password:API().post('/api/v1/auth/login',{'username':name,'password':password}).status_code
    for _ in range(6):
        assert attempt('wrong')==401
    for _ in range(15):
        assert attempt(TEST_PASSWORD)==200
    for _ in range(4):
        assert attempt('wrong')==401
    # the 11th failure in the window is still limited, and so is a correct password afterwards
    assert attempt('wrong')==429
    assert attempt(TEST_PASSWORD)==429
    assert attempt('wrong')==429

def test_failed_logins_alone_still_lock_the_username(factory):
    from .conftest import API,TEST_PASSWORD
    user,_,_=factory('EMPLOYEE')
    codes=[API().post('/api/v1/auth/login',{'username':user.username,'password':'wrong'}).status_code for _ in range(12)]
    assert codes==[401]*10+[429]*2
    assert API().post('/api/v1/auth/login',{'username':user.username,'password':TEST_PASSWORD}).status_code==429

# 9. loose validation
import datetime as dt

@pytest.mark.parametrize('birthday',['0001-01-01','1899-12-31','2099-01-01','9999-12-31'])
def test_birthday_must_be_realistic(hr,org,birthday):
    _,_,api=hr
    response=api.post('/api/v1/employees',emp_body(org,birthday=birthday))
    assert response.status_code==400 and response.json()['code']=='VALIDATION_ERROR'

def test_birthday_range_edges_are_accepted(hr,org):
    _,_,api=hr
    from django.utils import timezone
    for i,day in enumerate(['1900-01-01',timezone.localdate().isoformat(),'1985-02-28']):
        assert api.post('/api/v1/employees',emp_body(org,displayName=f'Born {chr(97+i)}{chr(98+i)}',birthday=day)).status_code==201
    assert (timezone.localdate()+dt.timedelta(days=1)).isoformat() and api.post('/api/v1/employees',emp_body(org,displayName='Tomorrow Born',birthday=(timezone.localdate()+dt.timedelta(days=1)).isoformat())).status_code==400

@pytest.mark.parametrize('field',['documentDate','effectiveDate'])
@pytest.mark.parametrize('value',['0001-01-01','1989-12-31','2101-01-01'])
def test_document_dates_are_bounded(hr,field,value):
    _,_,api=hr
    response=api.post('/api/v1/documents',{'title':'Dated','category':'OTHER',field:value})
    assert response.status_code==400 and response.json()['code']=='VALIDATION_ERROR'
    ok=api.post('/api/v1/documents',{'title':'Dated','category':'OTHER','documentDate':'1990-01-01','effectiveDate':'2100-12-31'})
    assert ok.status_code==201,ok.content

def test_effective_date_before_document_date_stays_allowed(hr):
    # REQUIREMENTS.md treats both dates as independent optional attributes (retroactive effect is legitimate).
    _,_,api=hr
    assert api.post('/api/v1/documents',{'title':'Retro','category':'OTHER','documentDate':'2024-05-01','effectiveDate':'2024-01-01'}).status_code==201

@pytest.mark.parametrize('phone',['+++','+','()','12-3','+9 8'])
def test_phone_needs_digits(hr,org,phone):
    _,_,api=hr
    assert api.post('/api/v1/employees',emp_body(org,mobilePhone=phone)).status_code==400
    assert api.patch('/api/v1/me/contact',{'mobilePhone':phone}).status_code==400

def test_valid_phones_and_blank_are_accepted(hr,org):
    _,_,api=hr
    for phone in ['+998 90 123 45 67','+998(90)1234567','12345','']:
        assert api.patch('/api/v1/me/contact',{'mobilePhone':phone}).status_code==200,phone

@pytest.mark.parametrize('extension',['abc','12a','1 2','+12','12-34'])
def test_extension_is_digits_only(hr,org,extension):
    _,_,api=hr
    assert api.post('/api/v1/employees',emp_body(org,extension=extension)).status_code==400

def test_extension_digits_and_blank_accepted(hr,org):
    _,_,api=hr
    for i,extension in enumerate(['201','8790','']):
        assert api.post('/api/v1/employees',emp_body(org,displayName=f'Ext person {chr(97+i)}{chr(98+i)}',extension=extension)).status_code==201

@pytest.mark.parametrize('bad',['Line\nbreak','Tab\tname','Nul\x00name','Esc\x1bname','Bidi '+chr(0x202e)+'name','Iso '+chr(0x2066)+'name','Mark '+chr(0x200f)+'name','Del\x7fname','Cr\rname'])
def test_display_name_rejects_control_and_bidi_characters(hr,org,bad):
    _,_,api=hr
    response=api.post('/api/v1/employees',emp_body(org,displayName=bad,confirmNamesake=True))
    assert response.status_code==400 and response.json()['code']=='VALIDATION_ERROR'

def test_normal_international_names_still_pass(hr,org):
    _,_,api=hr
    for name in ['Aliyev Aziz Akmalovich','Каримова Малика','Rö Şağ Çalış',"O'Neil-Smith Jr."]:
        assert api.post('/api/v1/employees',emp_body(org,displayName=name)).status_code==201,name

# 11. PostgreSQL-only suspicions, covered at the validation layer
@pytest.mark.parametrize('path',['/api/v1/employees','/api/v1/news','/api/v1/documents','/api/v1/search'])
@pytest.mark.parametrize('q',['a\x00b','\x00'])
def test_nul_in_search_text_is_400(hr,path,q):
    _,_,api=hr
    response=api.get(path,{'q':q})
    assert response.status_code==400 and response.json()['code']=='VALIDATION_ERROR'
    assert api.get(path,{'q':'ab'}).status_code==200

@pytest.mark.parametrize('path,param',[('/api/v1/news','category'),('/api/v1/documents','category'),('/api/v1/employees','officeId')])
def test_nul_in_any_query_parameter_is_400(hr,path,param):
    _,_,api=hr
    assert api.get(path,{param:'x\x00y'}).status_code==400
    assert api.get(path,{'a\x00':'y'}).status_code==400

def test_services_reject_nul_in_q_directly(hr):
    from portal import services as svc
    from portal.errors import PortalError
    user=hr[0]
    for call in [lambda:svc.employees(user,{'q':'a\x00'}),lambda:svc.news(user,{'q':'a\x00'}),lambda:svc.documents(user,{'q':'a\x00'}),lambda:svc.documents(user,{'q':'a\x00','history':'true'}),lambda:svc.search(user,'a\x00')]:
        with pytest.raises(PortalError) as error:
            call()
        assert error.value.detail['code']=='VALIDATION_ERROR'

def test_work_email_is_limited_to_the_column_width(hr,org):
    _,_,api=hr
    domain='.'.join(['b'*60]*4)
    fits='a@'+domain[:240]+'.org'
    assert len(fits)<=254
    too_long='a'*20+'@'+domain+'.'+'c'*60+'.org'
    assert len(too_long)>254
    response=api.post('/api/v1/employees',emp_body(org,workEmail=too_long))
    assert response.status_code==400,response.content
    assert 'workEmail' in str(response.json())
    assert api.post('/api/v1/employees',emp_body(org,workEmail=fits)).status_code==201

def test_news_order_is_explicit_about_null_publication_dates(hr):
    from portal import services as svc
    user,_,api=hr
    older=m.News.objects.create(title='Older',body='B',author=user,status='PUBLISHED',published_at='2030-01-01T00:00:00Z')
    newer=m.News.objects.create(title='Newer',body='B',author=user,status='PUBLISHED',published_at='2031-01-01T00:00:00Z')
    draft=m.News.objects.create(title='Draft',body='B',author=user,status='DRAFT')
    items=[x['id'] for x in api.get('/api/v1/news',{'manage':'true'}).json()['items']]
    assert items==[str(newer.id),str(older.id),str(draft.id)]
    import re
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    with CaptureQueriesContext(connection) as queries:
        svc.news(user,{'manage':'true'})
    assert any(re.search(r'ORDER BY .*"published_at" DESC NULLS LAST',q['sql']) for q in queries)

# 12. server-side traceback for unexpected errors
def test_unexpected_error_logs_traceback_but_response_stays_generic(hr,monkeypatch,caplog):
    from portal import services as svc
    def boom(*args,**kwargs):
        raise RuntimeError('secret-internal-detail')
    monkeypatch.setattr(svc,'units',boom)
    _,_,api=hr
    with caplog.at_level('ERROR',logger='portal'):
        response=api.get('/api/v1/org-units')
    assert response.status_code==500 and response.json()=={'code':'INTERNAL_ERROR'}
    assert 'secret-internal-detail' not in response.content.decode()
    record=next(r for r in caplog.records if r.name=='portal')
    assert record.exc_info and record.exc_info[0] is RuntimeError
    assert 'Traceback' in caplog.text and 'secret-internal-detail' in caplog.text

# 13. global search also looks inside document contents
def search_docs(api,q):
    response=api.get('/api/v1/search',{'q':q})
    assert response.status_code==200,response.content
    return response.json()['documents']

@pytest.mark.parametrize('role',['hr','employee'])
def test_global_search_finds_words_only_present_in_document_text(hr,employee,role):
    from .test_content_and_files import create_doc
    d,_,_=create_doc(hr,new_text='zyxwordonly appears just inside the extracted text')
    api={'hr':hr,'employee':employee}[role][2]
    found=search_docs(api,'zyxwordonly')
    assert found['total']==1 and found['items'][0]['id']==str(d.id)
    assert found['items'][0]['snippet'].startswith('zyxwordonly')
    assert search_docs(api,'absentwordqq')['total']==0

def test_global_search_content_hits_follow_document_visibility(hr,employee):
    from .test_content_and_files import create_doc
    from django.utils import timezone
    d,_,new=create_doc(hr,new_text='hiddenneedle in text')
    api=employee[2]
    assert search_docs(api,'hiddenneedle')['total']==1
    d.status='DRAFT';d.save()
    assert search_docs(api,'hiddenneedle')['total']==0
    d.status='PUBLISHED';d.deleted_at=timezone.now();d.save()
    assert search_docs(api,'hiddenneedle')['total']==0
    d.deleted_at=None;d.save()
    new.file.security_status='QUARANTINED';new.file.save()
    assert search_docs(api,'hiddenneedle')['total']==0
    new.file.security_status='APPROVED';new.file.save()
    assert search_docs(api,'hiddenneedle')['total']==1

def test_global_search_content_query_cost_is_bounded(hr,employee):
    from .test_content_and_files import create_doc
    create_doc(hr,new_text='costword '+('long text '*100000))
    with CaptureQueriesContext(connection) as captured:
        result=svc.search(employee[0],'costword')
    assert result['documents']['total']==1 and len(result['documents']['items'][0]['snippet'])<=360
    assert len(result['documents']['items'])<=6
    assert not any('normalized_text' in q['sql'].split(' FROM ')[0] for q in captured)
