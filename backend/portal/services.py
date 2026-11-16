from datetime import datetime
import re
from bisect import bisect_right
from django.db.models import F, Q, Count, Value, BigIntegerField, IntegerField, ExpressionWrapper
from django.db.models.functions import Substr, StrIndex, Length, Greatest, Cast
from django.utils import timezone
from . import models as m
from .domain import normalize, search_fold, search_date, upcoming_birthday
from .errors import PortalError
from .security import domain_write, audit, managed
from .serializers import validated, EmployeeInput, UnitInput, OfficeInput, NewsInput, DocumentInput, VersionInput, employee_data

def get_or_error(queryset, **kwargs):
    result = queryset.filter(**kwargs).first()
    if result is None:
        raise PortalError('NOT_FOUND',404)
    return result

def current_reference(value, model, **conditions):
    """Recheck references after acquiring domain_write, not on a stale serializer object."""
    if value is None:
        return None
    result=model.objects.select_for_update().filter(pk=value.pk,**conditions).first()
    if result is None:
        raise PortalError('FILE_NOT_APPROVED' if model is m.FileObject else 'REFERENCE_CHANGED',409)
    return result

def search_text(value):
    """Search text as a bounded string; NUL bytes are rejected here because PostgreSQL cannot store or compare them."""
    value=str(value)
    if '\x00' in value:
        raise PortalError('VALIDATION_ERROR')
    return value[:200]

def page(queryset, query, mapper):
    try:
        number=max(1,min(10000,int(query.get('page',1))))
        limit=max(1,min(100,int(query.get('limit',24))))
    except (ValueError,TypeError):
        raise PortalError('INVALID_PAGINATION')
    return {'items':[mapper(x) for x in queryset[(number-1)*limit:number*limit]],'total':queryset.count(),'page':number,'limit':limit}

def employee_queryset():
    return m.Employee.objects.select_related('unit','office','manager','photo')

def duplicate_names(name, exclude=None):
    qs=m.Employee.objects.filter(normalized_name=normalize(name),status='ACTIVE')
    if exclude:
        qs=qs.exclude(id=exclude)
    return [{'id':str(e.id),'displayName':e.display_name,'employeeNumber':e.employee_number,'status':e.status} for e in qs[:20]]

def employees(user, query):
    privileged=managed(user)
    qs=employee_queryset().filter(status='ARCHIVED' if privileged and query.get('archived')=='true' else 'ACTIVE')
    q=search_text(query.get('q','')).strip()
    date=search_date(q,privileged)
    if q:
        if date:
            qs=qs.filter(**{f'birthday__{k}':v for k,v in date.items()})
            if not privileged:
                qs=qs.filter(birthday_visible=True)
        else:
            nq=normalize(q)
            condition=Q(normalized_name__contains=nq)|Q(normalized_position__contains=nq)|Q(office__normalized_name__contains=nq)|Q(unit__normalized_name__contains=nq)|Q(extension__icontains=q)|Q(work_email__icontains=q)|Q(floor__icontains=q)|Q(room__icontains=q)
            mobile=Q(mobile_phone__icontains=q)
            if not privileged:
                mobile &= Q(mobile_visible=True)
            qs=qs.filter(condition|mobile)
    if query.get('officeId'):
        qs=qs.filter(office_id=query['officeId'])
    if query.get('unitId'):
        ids={str(query['unitId'])}
        if query.get('descendants')=='true':
            remaining=True
            while remaining:
                children={str(x) for x in m.OrgUnit.objects.filter(parent_id__in=ids,active=True).values_list('id',flat=True)}
                remaining=bool(children-ids)
                ids|=children
        qs=qs.filter(unit_id__in=ids)
    if query.get('withoutPhoto')=='true':
        qs=qs.filter(photo__isnull=True)
    return page(qs.order_by('display_name','id'),query,lambda e:employee_data(e,user))

def employee(user, id):
    qs=employee_queryset()
    if not managed(user):
        qs=qs.filter(status='ACTIVE')
    e=get_or_error(qs,pk=id)
    result=employee_data(e,user)
    result['directReports']=[{'id':str(r.id),'displayName':r.display_name,'positionTitle':r.position_title} for r in e.reports.filter(status='ACTIVE').order_by('display_name')]
    return result

def save_employee(user,data,id=None):
    values=validated(EmployeeInput,data,partial=bool(id))
    confirm=values.pop('confirm_namesake',False)
    with domain_write():
        for key,model,conditions in [('unit',m.OrgUnit,{'active':True}),('office',m.Office,{'active':True}),('manager',m.Employee,{'status':'ACTIVE'}),('photo',m.FileObject,{'purpose':'PHOTO','security_status':'APPROVED'})]:
            if key in values:
                values[key]=current_reference(values[key],model,**conditions)
        obj=get_or_error(m.Employee.objects.select_for_update(),pk=id) if id else m.Employee()
        name=values.get('display_name',obj.display_name)
        # Namesakes matter when a person is created or renamed, not on every unrelated edit.
        matches=duplicate_names(name,id) if not id or normalize(name)!=obj.normalized_name else []
        if matches and not confirm:
            raise PortalError('DUPLICATE_NAME',409,{'matches':matches})
        manager=values.get('manager',obj.manager if id else None)
        seen=set()
        while manager:
            if manager.pk==obj.pk or manager.pk in seen:
                raise PortalError('MANAGER_CYCLE')
            seen.add(manager.pk)
            manager=manager.manager
        for key,value in values.items():
            setattr(obj,key,value)
        obj.save()
        audit(user,'EMPLOYEE_UPDATED' if id else 'EMPLOYEE_CREATED','EMPLOYEE',obj.id,values.keys())
        return {'id':str(obj.id),'employeeNumber':obj.employee_number}

def archive_employee(user,id,data):
    with domain_write():
        e=get_or_error(m.Employee.objects.select_for_update(),pk=id)
        if e.status=='ARCHIVED':
            raise PortalError('CONFLICT',409)
        accounts=m.UserAccount.objects.filter(employee=e,user__is_active=True)
        if accounts.filter(role='ADMIN').exists() and not m.UserAccount.objects.filter(role='ADMIN',user__is_active=True).exclude(employee=e).exists():
            raise PortalError('LAST_ADMIN',409)
        reports=e.reports.filter(status='ACTIVE')
        replacement=None
        if data.get('replacementManagerId'):
            replacement=get_or_error(m.Employee.objects.filter(status='ACTIVE'),pk=data['replacementManagerId'])
            cursor=replacement
            seen=set()
            while cursor:
                if cursor.pk==e.pk or cursor.pk in seen:
                    raise PortalError('MANAGER_CYCLE')
                seen.add(cursor.pk)
                cursor=cursor.manager
        if reports.exists() and not replacement and data.get('confirmDetach') is not True:
            raise PortalError('MANAGER_HAS_REPORTS',409,{'reports':[{'id':str(x.id),'displayName':x.display_name} for x in reports]})
        e.reports.update(manager=replacement)
        m.OrgUnit.objects.filter(head=e).update(head=None)
        for account in accounts:
            account.user.is_active=False
            account.user.save(update_fields=['is_active'])
            account.blocked_at=timezone.now()
            account.session_version+=1
            account.save(update_fields=['blocked_at','session_version','updated_at'])
        e.status,e.archived_at='ARCHIVED',timezone.now()
        e.save()
        audit(user,'EMPLOYEE_ARCHIVED','EMPLOYEE',id,['status','user.active'])
        return {'success':True}

def restore_employee(user,id):
    with domain_write():
        e=get_or_error(m.Employee.objects.select_for_update().select_related('unit','office'),pk=id)
        if e.status!='ARCHIVED':
            raise PortalError('CONFLICT',409)
        if not e.unit.active:
            raise PortalError('UNIT_INACTIVE',409)
        if not e.office.active:
            raise PortalError('OFFICE_INACTIVE',409)
        e.status,e.archived_at='ACTIVE',None
        e.save()
        audit(user,'EMPLOYEE_RESTORED','EMPLOYEE',id,['status'])
    return {'success':True,'accountRemainsBlocked':True}

def self_update(user,data,serializer):
    values=validated(serializer,data,partial=True)
    if not user.portal_account.employee_id:
        raise PortalError('NO_EMPLOYEE_LINK',409)
    with domain_write():
        e=get_or_error(m.Employee.objects.select_for_update().filter(status='ACTIVE'),pk=user.portal_account.employee_id)
        for key,value in values.items():
            setattr(e,key,value)
        e.save()
        audit(user,'SELF_UPDATED','EMPLOYEE',e.id,values.keys())
    return {'success':True}

def units():
    return [{'id':str(u.id),'name':u.name,'type':u.type,'parentId':str(u.parent_id) if u.parent_id else None,'headEmployeeId':str(u.head_id) if u.head_id else None,'headName':u.head.display_name if u.head else None,'active':u.active,'employeeCount':u.employee_count,'sortOrder':u.sort_order} for u in m.OrgUnit.objects.filter(active=True).select_related('head').annotate(employee_count=Count('employees',filter=Q(employees__status='ACTIVE'))).order_by('sort_order','name')]

def offices():
    return [{'id':str(o.id),'name':o.name,'active':o.active} for o in m.Office.objects.filter(active=True).order_by('name')]

def save_unit(user,data,id=None):
    values=validated(UnitInput,data,partial=bool(id))
    with domain_write():
        for key,model,conditions in [('parent',m.OrgUnit,{'active':True}),('head',m.Employee,{'status':'ACTIVE'})]:
            if key in values:
                values[key]=current_reference(values[key],model,**conditions)
        obj=get_or_error(m.OrgUnit.objects.select_for_update(),pk=id) if id else m.OrgUnit()
        parent=parent_unit=values.get('parent',obj.parent if id else None)
        seen=set()
        while parent:
            if parent.pk==obj.pk or parent.pk in seen:
                raise PortalError('UNIT_CYCLE')
            seen.add(parent.pk)
            parent=parent.parent
        active=values.get('active',obj.active if id else True)
        if active and m.OrgUnit.objects.filter(normalized_name=normalize(values.get('name',obj.name)),active=True).exclude(pk=obj.pk).exists():
            # Imports resolve a unit by its normalized name among all active units, so that must be unique.
            raise PortalError('UNIT_ALREADY_EXISTS',409)
        if active and parent_unit and not parent_unit.active:
            raise PortalError('UNIT_INACTIVE',409)
        if values.get('active') is False and id and (obj.employees.filter(status='ACTIVE').exists() or obj.children.filter(active=True).exists()):
            raise PortalError('UNIT_IN_USE',409)
        for key,value in values.items():
            setattr(obj,key,value)
        obj.save()
        audit(user,'UNIT_UPDATED' if id else 'UNIT_CREATED','UNIT',obj.id,values.keys())
        return {'id':str(obj.id)}

def save_office(user,data,id=None):
    values=validated(OfficeInput,data,partial=bool(id))
    with domain_write():
        obj=get_or_error(m.Office.objects.select_for_update(),pk=id) if id else m.Office()
        if values.get('active') is False and id and obj.employees.filter(status='ACTIVE').exists():
            raise PortalError('OFFICE_IN_USE',409)
        if values.get('active',obj.active if id else True) and m.Office.objects.filter(normalized_name=normalize(values.get('name',obj.name)),active=True).exclude(id=obj.pk).exists():
            raise PortalError('OFFICE_ALREADY_EXISTS',409)
        for key,value in values.items():
            setattr(obj,key,value)
        obj.save()
        audit(user,'OFFICE_UPDATED' if id else 'OFFICE_CREATED','OFFICE',obj.id,values.keys())
        return {'id':str(obj.id)}

def birthdays(user):
    result=[]
    for e in employee_queryset().filter(status='ACTIVE',birthday__isnull=False,birthday_visible=True):
        result.append({'id':str(e.id),'displayName':e.display_name,'positionTitle':e.position_title,'officeName':e.office.name,'photoFileId':str(e.photo_id) if e.photo_id else None,**upcoming_birthday(e.birthday)})
    return sorted(result,key=lambda e:e['days'])[:12]

def published_news():
    now=timezone.now()
    return m.News.objects.filter(deleted_at=None,status='PUBLISHED').filter(Q(published_at=None)|Q(published_at__lte=now)).filter(Q(expires_at=None)|Q(expires_at__gt=now))

def news_data(n,full=False):
    value={'id':str(n.id),'slug':str(n.id),'title':n.title,'summary':n.summary,'category':n.category,'language':n.language,'translations':n.translations,'status':n.status,'pinned':n.pinned,'publishedAt':n.published_at.isoformat() if n.published_at else None,'expiresAt':n.expires_at.isoformat() if n.expires_at else None,'updatedAt':n.updated_at.isoformat(),'authorName':getattr(n.author,'portal_account',None).display_name if hasattr(n.author,'portal_account') else n.author.username,'coverFileId':str(n.cover_id) if n.cover_id else None,'views':n.view_count if hasattr(n,'view_count') else n.views.count()}
    if full:
        value['body']=n.body
    return value

def news(user,query):
    qs=m.News.objects.filter(deleted_at=None) if managed(user) and query.get('manage')=='true' else published_news()
    if query.get('q'):
        raw=search_text(query['q'])
        qs=qs.filter(Q(normalized_title__contains=normalize(raw))|Q(summary__icontains=raw))
    if query.get('pinned')=='true':
        qs=qs.filter(pinned=True)
    if query.get('category'):
        category=query['category']
        # NEWS is the original persisted value; COMPANY is its UI alias.
        qs=qs.filter(category__in=['NEWS','COMPANY']) if category in {'NEWS','COMPANY'} else qs.filter(category=category)
    return page(qs.select_related('author__portal_account','cover').annotate(view_count=Count('views')).order_by(F('published_at').desc(nulls_last=True),'-created_at'),query,news_data)

def article(user,id):
    qs=m.News.objects.filter(deleted_at=None) if managed(user) else published_news()
    return news_data(get_or_error(qs.select_related('author__portal_account'),pk=id),True)

def save_news(user,data,id=None):
    values=validated(NewsInput,data,partial=bool(id))
    with domain_write():
        if 'cover' in values:
            values['cover']=current_reference(values['cover'],m.FileObject,purpose='COVER',security_status='APPROVED')
        obj=get_or_error(m.News.objects.select_for_update().filter(deleted_at=None),pk=id) if id else m.News(author=user)
        if id:
            m.NewsRevision.objects.create(news=obj,edited_by=user,snapshot={'title':obj.title,'summary':obj.summary,'body':obj.body,'language':obj.language,'translations':obj.translations,'status':obj.status,'pinned':obj.pinned})
        for key,value in values.items():
            setattr(obj,key,value)
        if obj.published_at and obj.expires_at and obj.expires_at<=obj.published_at:
            raise PortalError('VALIDATION_ERROR',400,{'expiresAt':['Must be later than publishedAt.']})
        if obj.status=='PUBLISHED' and not obj.published_at:
            obj.published_at=timezone.now()
        obj.save()
        audit(user,'NEWS_UPDATED' if id else 'NEWS_CREATED','NEWS',obj.id,values.keys())
        return {'id':str(obj.id)}

def view_news(user,id):
    obj=get_or_error(published_news(),pk=id)
    m.NewsView.objects.get_or_create(news=obj,user=user)
    return {'views':obj.views.count()}

def version_data(v,document=None,snippet=False):
    value={'id':str(v.id),'label':v.label,'documentDate':v.document_date,'effectiveDate':v.effective_date,'changeNote':v.change_note,'fileId':str(v.file_id),'fileName':v.file.original_name,'mimeType':v.file.mime_type,'fileSize':v.file.size_bytes,'securityStatus':v.file.security_status,'extractionStatus':v.extraction_status,'extractionError':v.extraction_error,'ocrQuality':v.ocr_quality,'createdAt':v.created_at}
    if document:
        value['outdated']=document.current_version_id!=v.id
        value['currentVersionId']=str(document.current_version_id) if document.current_version_id else None
    if snippet:
        value['snippet']=v.extracted_text[:360]
    return value

def document_data(d,v=None,inside=False):
    v=v or d.current_version
    result={'id':str(d.id),'title':d.title,'number':d.number,'documentDate':d.document_date,'effectiveDate':d.effective_date,'category':d.category,'description':d.description,'language':d.language,'translations':d.translations,'status':d.status,'ownerUnitId':str(d.owner_unit_id) if d.owner_unit_id else None,'currentVersionId':str(d.current_version_id) if d.current_version_id else None,'updatedAt':d.updated_at}
    if v:
        result.update({'versionId':str(v.id),'versionLabel':v.label,'securityStatus':v.file.security_status,'mimeType':v.file.mime_type,'fileSize':v.file.size_bytes,'extractionStatus':v.extraction_status,'outdated':v.id!=d.current_version_id,'snippet':getattr(v,'search_snippet','') or '' if inside else ''})
        if inside and getattr(v,'snippet_match',None):
            result['snippetMatch']=v.snippet_match
    return result

SNIPPET_HEAD,SNIPPET_REACH,SNIPPET_WIDE=360,2500,20000

def snippet_columns(q,prefix='',reach=SNIPPET_REACH):
    """Bounded text window around the first match. The database finds the match in the folded text, scales that offset to
    the original text (folding only shortens it a little) and cuts a fixed-size window; Python then finds the exact match."""
    text,folded=prefix+'extracted_text',prefix+'normalized_text'
    folded_query=search_fold(q)
    hit=StrIndex(folded,Value(folded_query))
    scaled=ExpressionWrapper(Cast(hit,BigIntegerField())*Length(text)/Greatest(Length(folded),Value(1)),output_field=BigIntegerField())
    start=Cast(Greatest(Value(1),scaled-Value(reach)),IntegerField())
    return {'snippet_hit':hit,'snippet_from':start,'snippet_window':Substr(text,start,2*reach+len(folded_query))}

def locate_match(window,folded_query):
    """(start,end) of the first text in `window` whose folded form contains folded_query; folding is applied word by word so offsets map back."""
    words=[(w.start(),w.end(),search_fold(w.group())) for w in re.finditer(r'\S+',window)]
    words=[w for w in words if w[2]]
    if not folded_query or not words:
        return None
    starts,position=[],0
    for _,_,folded in words:
        starts.append(position)
        position+=len(folded)+1
    index=' '.join(w[2] for w in words).find(folded_query)
    if index<0:
        return None
    def edge(i,offset,last):
        word_start,word_end,folded=words[i]
        word=window[word_start:word_end]
        if len(word)>200:
            return word_end if last else word_start
        if last:
            size=max((n for n in range(len(word)+1) if len(search_fold(word[:n]))<=offset),default=0)
            return word_start+size
        return word_start+next((n for n in range(1,len(word)+1) if len(search_fold(word[:n]))>offset),len(word))-1
    first=bisect_right(starts,index)-1
    last=bisect_right(starts,index+len(folded_query)-1)-1
    return edge(first,index-starts[first],False),edge(last,index+len(folded_query)-starts[last],True)

def utf16(value):
    return len(value.encode('utf-16-le','surrogatepass'))//2

def context_snippet(window,span,more_before,more_after):
    """About 360 characters around span, cut at word edges. Returns (text, [start,end] of the match in UTF-16 units)."""
    start,end=span
    left=max(0,start-110)
    for _ in range(15):  # begin on a word edge
        if 0<left<start and not window[left-1].isspace():
            left+=1
    right=max(end,min(len(window),left+SNIPPET_HEAD-2))  # room for both ellipses
    for _ in range(15):  # and finish on one
        if end<right<len(window) and not window[right].isspace():
            right-=1
    flat=lambda value:re.sub(r'\s+',' ',value)
    before,match,after=flat(window[left:start]),flat(window[start:end]),flat(window[end:right])
    lead='…' if left>0 or more_before else ''
    tail='…' if right<len(window) or more_after else ''
    begin=utf16(lead+before)
    return lead+before+match+after+tail,[begin,begin+utf16(match)]

def attach_snippets(rows,q,reach=SNIPPET_REACH):
    """rows: (version_id,head,hit,window,window_start). Returns {version_id:(snippet,match|None)}; the head of the text when nothing is located."""
    result,missed=({},[])
    folded_query=search_fold(q)
    for version_id,head,hit,window,window_from in rows:
        found=locate_match(window or '',folded_query) if hit and window else None
        if found:
            result[version_id]=context_snippet(window,found,window_from>1,len(window)>=2*reach)
        else:
            result[version_id]=(head or '',None)
            if hit and reach<SNIPPET_WIDE:
                missed.append(version_id)
    if missed:
        wide=m.DocumentVersion.objects.filter(pk__in=missed).annotate(**snippet_columns(q,reach=SNIPPET_WIDE)).values_list('pk','snippet_hit','snippet_window','snippet_from')
        result.update(attach_snippets([(pk,result[pk][0],hit,window,start) for pk,hit,window,start in wide],q,SNIPPET_WIDE))
    return result

def snippet_page(queryset,query,q,inside,version_of,mapper):
    """Page of documents/versions; with inside search each row carries the text around its first match."""
    result=page(queryset,query,lambda row:row)
    rows=result['items']
    if inside:
        found=attach_snippets([(version_of(row).id,row.search_snippet,getattr(row,'snippet_hit',0),getattr(row,'snippet_window',''),getattr(row,'snippet_from',1)) for row in rows if version_of(row)],q) if q else {}
        for row in rows:
            version=version_of(row)
            if version:
                text,match=found.get(version.id) or (row.search_snippet or '',None)
                version.search_snippet,version.snippet_match=text,match
    result['items']=[mapper(row) for row in rows]
    return result

def documents(user,query):
    manage=managed(user) and query.get('manage')=='true'
    docs=m.Document.objects.filter(deleted_at=None)
    if not manage:
        docs=docs.filter(status='PUBLISHED',current_version__file__security_status='APPROVED')
    if query.get('category'):
        docs=docs.filter(category=query['category'])
    for key,lookup in [('from','document_date__gte'),('to','document_date__lte')]:
        if query.get(key):
            try:
                value=datetime.strptime(query[key],'%Y-%m-%d').date()
            except (TypeError,ValueError):
                raise PortalError('INVALID_DATE')
            docs=docs.filter(**{lookup:value})
    q=search_text(query.get('q',''))
    inside=query.get('inside')=='true'
    if query.get('history')=='true':
        versions=m.DocumentVersion.objects.filter(document__in=docs,file__security_status='APPROVED').select_related('document','file','document__current_version__file')
        if q:
            condition=Q(document__normalized_title__contains=normalize(q))|Q(document__number__icontains=q)
            if inside:
                condition|=Q(normalized_text__contains=search_fold(q),extraction_status='READY')
            versions=versions.filter(condition)
        versions=versions.defer('extracted_text','normalized_text','document__current_version__extracted_text','document__current_version__normalized_text').order_by('-document__updated_at','-created_at')
        if inside:
            versions=versions.annotate(search_snippet=Substr('extracted_text',1,SNIPPET_HEAD),**(snippet_columns(q) if q else {}))
        return snippet_page(versions,query,q,inside,lambda v:v,lambda v:document_data(v.document,v,inside))
    if q:
        condition=Q(normalized_title__contains=normalize(q))|Q(number__icontains=q)
        if inside:
            condition|=Q(current_version__normalized_text__contains=search_fold(q),current_version__extraction_status='READY')
        docs=docs.filter(condition)
    qs=docs.select_related('current_version__file').defer('current_version__extracted_text','current_version__normalized_text')
    if inside:
        qs=qs.annotate(search_snippet=Substr('current_version__extracted_text',1,SNIPPET_HEAD),**(snippet_columns(q,'current_version__') if q else {}))
    return snippet_page(qs.order_by('-updated_at'),query,q,inside,lambda d:d.current_version,lambda d:document_data(d,inside=inside))

def document(user,id):
    docs=m.Document.objects.filter(deleted_at=None)
    if not managed(user):
        docs=docs.filter(status='PUBLISHED',current_version__file__security_status='APPROVED')
    d=get_or_error(docs.select_related('current_version__file'),pk=id)
    versions=d.versions.select_related('file').order_by('-created_at')
    if not managed(user):
        versions=versions.filter(file__security_status='APPROVED')
    return {**document_data(d),'versions':[version_data(v,d) for v in versions]}

def save_document(user,data,id=None):
    values=validated(DocumentInput,data,partial=bool(id))
    with domain_write():
        obj=get_or_error(m.Document.objects.select_for_update().filter(deleted_at=None),pk=id) if id else m.Document(created_by=user)
        for key,value in values.items():
            setattr(obj,key,value)
        if obj.status=='PUBLISHED' and (not obj.current_version_id or obj.current_version.file.security_status!='APPROVED'):
            raise PortalError('DOCUMENT_FILE_REQUIRED')
        obj.save()
        audit(user,'DOCUMENT_UPDATED' if id else 'DOCUMENT_CREATED','DOCUMENT',obj.id,values.keys())
        return {'id':str(obj.id)}

def add_version(user,id,data):
    values=validated(VersionInput,data)
    with domain_write():
        values['file']=current_reference(values['file'],m.FileObject,purpose='DOCUMENT',security_status='APPROVED')
        d=get_or_error(m.Document.objects.select_for_update().filter(deleted_at=None),pk=id)
        v=m.DocumentVersion.objects.create(document=d,created_by=user,**values)
        d.current_version=v
        if v.document_date:
            d.document_date=v.document_date
        if v.effective_date:
            d.effective_date=v.effective_date
        d.save()
        job,created=m.ProcessingJob.objects.get_or_create(file=v.file,kind='EXTRACT')
        if not created and job.status not in {'PENDING','RUNNING'}:
            job.status,job.attempts,job.error_code='PENDING',0,''
            job.lease_hash,job.lease_expires,job.finished_at='',None,None
            job.save(update_fields=['status','attempts','error_code','lease_hash','lease_expires','finished_at'])
        audit(user,'VERSION_ADDED','DOCUMENT',d.id,['currentVersionId'],metadata={'versionId':str(v.id)})
        return {'id':str(v.id)}

def content_model(kind):
    if kind not in {'news','documents'}:
        raise PortalError('INVALID_CONTENT_TYPE')
    return m.News if kind=='news' else m.Document

def trash(user,kind,id):
    with domain_write():
        obj=get_or_error(content_model(kind).objects.select_for_update().filter(deleted_at=None),pk=id)
        obj.deleted_at=timezone.now()
        obj.save()
        audit(user,'TRASHED',kind.upper(),id,['deletedAt'])
    return {'success':True}

def trash_list():
    result=[]
    for kind in ['news','documents']:
        result.extend({'id':str(o.id),'title':o.title,'kind':kind,'deletedAt':o.deleted_at} for o in content_model(kind).objects.filter(deleted_at__isnull=False))
    return sorted(result,key=lambda x:x['deletedAt'],reverse=True)[:200]

def restore_content(user,kind,id):
    with domain_write():
        obj=get_or_error(content_model(kind).objects.select_for_update().filter(deleted_at__isnull=False),pk=id)
        obj.deleted_at=None
        obj.save()
        audit(user,'RESTORED',kind.upper(),id,['deletedAt'])
    return {'success':True}

def permanent_delete(user,kind,id,confirmation):
    orphan_ids=[]
    with domain_write():
        obj=get_or_error(content_model(kind).objects.select_for_update().filter(deleted_at__isnull=False),pk=id)
        if confirmation!=obj.title:
            raise PortalError('CONFIRMATION_REQUIRED')
        if kind=='documents':
            orphan_ids=list(obj.versions.values_list('file_id',flat=True))
            obj.current_version=None
            obj.save(update_fields=['current_version'])
        else:
            if obj.cover_id:
                orphan_ids=[obj.cover_id]
        obj.delete()
        audit(user,'PERMANENTLY_DELETED',kind.upper(),id)
        for file in m.FileObject.objects.select_for_update().filter(id__in=orphan_ids):
            if not file.document_versions.exists() and not file.news_covers.exists() and not file.employee_photos.exists():
                # Revoke access and in-flight leases atomically with the deletion.
                file.security_status='REJECTED'
                file.rejection_code='DELETED'
                file.parsed_data=None
                file.save(update_fields=['security_status','rejection_code','parsed_data'])
                file.jobs.filter(status__in=['PENDING','RUNNING']).update(status='CANCELLED',lease_hash='',lease_expires=None,finished_at=timezone.now(),error_code='FILE_DELETED')
    return {'success':True,'storagePurgeQueued':bool(orphan_ids)}

def search(user,q):
    return {'employees':employees(user,{'q':q,'limit':6}),'news':news(user,{'q':q,'limit':6}),'documents':documents(user,{'q':q,'limit':6,'inside':'true'}),'units':[u for u in units() if normalize(q) in normalize(u['name'])][:6]}
