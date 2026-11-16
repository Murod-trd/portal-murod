import hashlib
import json
from django.utils import timezone
from . import models as m
from .domain import normalize,spreadsheet_restore
from .errors import PortalError, error_codes
from .security import audit,domain_write
from .serializers import EmployeeInput
from .services import get_or_error,duplicate_names,save_employee

FIELDS={'displayName':['displayName','ФИО','FIO','fio'],'positionTitle':['positionTitle','Должность','Lavozim'],'unit':['unit','Подразделение','Bölim',"Bo‘lim"],'office':['office','Офис','Ofis'],'employeeNumber':['employeeNumber','Номер сотрудника'],'managerNumber':['managerNumber','Номер руководителя'],'extension':['extension','Внутренний номер'],'mobilePhone':['mobilePhone','Мобильный номер'],'workEmail':['workEmail','Email'],'birthday':['birthday','Дата рождения'],'floor':['floor','Этаж'],'room':['room','Кабинет']}

FIELDS['extension'].append('Ichki raqam')
FIELDS['mobilePhone'].append('Mobil raqam')
FIELDS['birthday'].append('Tug‘ilgan sana')
FIELDS['floor'].append('Qavat')
FIELDS['room'].append('Xona')
FIELDS['employeeNumber'].append('Xodim raqami')
FIELDS['managerNumber'].append('Rahbar raqami')

FIELDS['displayName'].append('Full name')
FIELDS['positionTitle'].append('Job title')
FIELDS['unit'].append('Department')
FIELDS['office'].append('Office')
FIELDS['extension'].append('Internal extension')
FIELDS['mobilePhone'].append('Mobile number')
FIELDS['birthday'].append('Date of birth')
FIELDS['floor'].append('Floor')
FIELDS['room'].append('Room')
FIELDS['employeeNumber'].append('Employee number')
FIELDS['managerNumber'].append('Manager number')

def preview_import(user,file_id,mapping=None):
    file=get_or_error(m.FileObject.objects,purpose='IMPORT',pk=file_id)
    if file.security_status!='APPROVED':
        raise PortalError('FILE_NOT_APPROVED',409)
    if file.parsed_data is None:
        error=file.jobs.filter(kind='PARSE_IMPORT',status='ERROR').first()
        raise PortalError(error.error_code if error else 'IMPORT_PROCESSING',409)
    headers=file.parsed_data['headers']
    if mapping is not None and (not isinstance(mapping,dict) or set(mapping)-set(FIELDS) or any(v not in headers for v in mapping.values() if v)):
        raise PortalError('INVALID_MAPPING')
    suggested={}
    for field,aliases in FIELDS.items():
        match=next((header for header in headers if normalize(header) in {normalize(alias) for alias in aliases}),None)
        if match:
            suggested[field]=match
    mapping=mapping if mapping is not None else suggested
    columns={key:headers.index(value) for key,value in mapping.items() if value in headers}
    prepared=[]
    errors=[]
    numbers=file.parsed_data.get('rowNumbers')
    if not isinstance(numbers,list) or len(numbers)!=len(file.parsed_data['rows']):
        # Older parsed data has no real row numbers: assume no blank rows were dropped.
        numbers=list(range(2,len(file.parsed_data['rows'])+2))
    for index,cells in enumerate(file.parsed_data['rows']):
        row={key:spreadsheet_restore(str(cells[pos]).strip()) if pos<len(cells) else '' for key,pos in columns.items()}
        data={key:value for key,value in row.items() if key in {'displayName','positionTitle','extension','mobilePhone','workEmail','birthday','floor','room'} and value!=''}
        row_errors=[]
        if data.get('displayName','').startswith(('ПРИМЕР:','NAMUNA:','EXAMPLE:')):
            row_errors.append({'field':'displayName','code':'TEMPLATE_EXAMPLE_NOT_REPLACED'})
        resolved={}
        for field,model in [('unit',m.OrgUnit),('office',m.Office)]:
            found=list(model.objects.filter(normalized_name=normalize(row.get(field,'')),active=True)[:2])
            resolved[field]=found[0].name if len(found)==1 else None
            if len(found)!=1:
                row_errors.append({'field':field,'code':'UNKNOWN_OR_AMBIGUOUS_REFERENCE'})
            else:
                data[field+'Id']=str(found[0].pk)
        manager_number=row.get('managerNumber')
        if manager_number:
            manager=m.Employee.objects.filter(employee_number=manager_number,status='ACTIVE').first()
            if manager:
                data['managerId']=str(manager.id)
                resolved['manager']=manager.display_name
            else:
                row_errors.append({'field':'managerNumber','code':'INVALID_MANAGER'})
        if row.get('birthday') and '.' in row['birthday']:
            from datetime import datetime
            try:
                data['birthday']=datetime.strptime(row['birthday'],'%d.%m.%Y').date().isoformat()
            except ValueError:
                row_errors.append({'field':'birthday','code':'INVALID_DATE'})
        serializer=EmployeeInput(data=data)
        if not serializer.is_valid():
            # A reference or date already reported above is not reported a second time as a missing/invalid serializer field.
            covered={{'unit':'unitId','office':'officeId','managerNumber':'managerId','birthday':'birthday'}.get(e['field']) for e in row_errors}
            remaining={key:value for key,value in serializer.errors.items() if key not in covered}
            if remaining:
                row_errors.append({'field':'row','code':'VALIDATION_ERROR','details':remaining,'fieldCodes':error_codes(remaining)})
        existing=None
        if row.get('employeeNumber'):
            existing=m.Employee.objects.filter(employee_number=row['employeeNumber']).first()
            if not existing:
                row_errors.append({'field':'employeeNumber','code':'UNKNOWN_EMPLOYEE_NUMBER'})
        matches=duplicate_names(data.get('displayName',''),existing.pk if existing else None)
        # A repeated name inside this same file must also be explicitly confirmed.
        in_file=any(normalize(p['data'].get('displayName',''))==normalize(data.get('displayName','')) for p in prepared)
        prepared.append({'row':numbers[index],'data':data,'employeeNumber':row.get('employeeNumber',''),'existingId':str(existing.id) if existing else None,'updatedAt':existing.updated_at.isoformat() if existing else None,'matches':matches,'sameNameInFile':in_file,'errors':row_errors,'entered':{key:row.get(key,'') for key in ['unit','office','managerNumber']},'resolved':resolved,'suggestedAction':'UPDATE' if existing else ('CHOOSE' if matches or in_file else 'NEW')})
        if row_errors:
            errors.append({'row':numbers[index],'errors':row_errors})
    digest=hashlib.sha256(json.dumps(prepared,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    job=m.ImportJob.objects.create(file=file,created_by=user,rows=prepared,errors=errors,preview_hash=digest)
    audit(user,'IMPORT_PREVIEW','IMPORT',job.id)
    return {'id':str(job.id),'headers':headers,'mapping':mapping,'rows':prepared,'errors':errors,'total':len(prepared),'previewHash':digest}

def commit_import(user,id,body):
    if set(body)-{'previewHash','decisions'} or not isinstance(body.get('decisions',{}),dict) or not all(isinstance(v,str) for v in body.get('decisions',{}).values()):
        raise PortalError('INVALID_IMPORT_COMMIT')
    with domain_write():
        job=get_or_error(m.ImportJob.objects.select_for_update(),pk=id)
        if job.status!='PREVIEW':
            raise PortalError('IMPORT_ALREADY_COMMITTED',409)
        if body.get('previewHash')!=job.preview_hash or job.errors:
            raise PortalError('IMPORT_HAS_ERRORS',409)
        if job.file.security_status!='APPROVED':
            raise PortalError('FILE_NOT_APPROVED',403)
        decisions=body.get('decisions',{})
        report={'created':0,'updated':0,'skipped':0,'items':[]}
        for row in job.rows:
            decision=decisions.get(str(row['row']),row['suggestedAction'])
            if decision=='SKIP':
                report['skipped']+=1
                continue
            if decision not in {'NEW','UPDATE','NAMESAKE'}:
                raise PortalError('IMPORT_DECISION_REQUIRED',409,{'row':row['row']})
            target=None
            if decision=='UPDATE':
                if not row['existingId']:
                    raise PortalError('IMPORT_TARGET_REQUIRED',409,{'row':row['row']})
                target=get_or_error(m.Employee.objects.select_for_update(),pk=row['existingId'])
                if target.updated_at.isoformat()!=row['updatedAt']:
                    raise PortalError('STALE_IMPORT_PREVIEW',409,{'row':row['row']})
            elif row['existingId']:
                # A stable employee number is never silently assigned to a different person.
                raise PortalError('IMPORT_NUMBER_CONFLICT',409,{'row':row['row']})
            if decision=='NEW' and (row['matches'] or row['sameNameInFile']):
                raise PortalError('IMPORT_DECISION_REQUIRED',409,{'row':row['row']})
            data={**row['data'],'confirmNamesake':decision in {'UPDATE','NAMESAKE'}}
            item=save_employee(user,data,str(target.id) if target else None)
            report['updated' if target else 'created']+=1
            report['items'].append({'row':row['row'],**item})
        job.status='COMMITTED'
        job.committed_at=timezone.now()
        job.report=report
        job.save(update_fields=['status','committed_at','report'])
        audit(user,'IMPORT_COMMITTED','IMPORT',job.id,metadata={key:report[key] for key in ['created','updated','skipped']})
        return report
