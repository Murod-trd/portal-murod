from .contracts import response_for
import csv
import io
from django.conf import settings
from django.db import connection
from django.http import HttpResponse, FileResponse, JsonResponse
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema, OpenApiTypes
from . import models as m,services as svc
from .serializers import EmployeeInput, UnitInput, OfficeInput, NewsInput, DocumentInput, VersionInput, SelfContact, SelfLocation,SelfProfile
from .security import audit, managed
from .domain import spreadsheet_safe
from .errors import PortalError
from .imports import preview_import,commit_import

class ManagedView(APIView):
    required_roles={'HR','ADMIN'}

class EmployeesView(APIView):
    @extend_schema(operation_id='EmployeesView_get')
    @extend_schema(responses=response_for('EmployeesView','get'))
    def get(self,request):
        return Response(svc.employees(request.user,request.query_params))
    @extend_schema(operation_id='EmployeesView_post')
    @extend_schema(request=EmployeeInput,responses=response_for('EmployeesView','post'))
    def post(self,request):
        if not managed(request.user):
            audit(request.user,'ACCESS_DENIED','EMPLOYEE',outcome='DENIED')
            raise PortalError('FORBIDDEN',403)
        return Response(svc.save_employee(request.user,request.data),status=201)

class EmployeeView(APIView):
    @extend_schema(operation_id='EmployeeView_get')
    @extend_schema(responses=response_for('EmployeeView','get'))
    def get(self,request,id):
        return Response(svc.employee(request.user,id))
    @extend_schema(operation_id='EmployeeView_patch')
    @extend_schema(request=EmployeeInput,responses=response_for('EmployeeView','patch'))
    def patch(self,request,id):
        if not managed(request.user):
            audit(request.user,'ACCESS_DENIED','EMPLOYEE',id,outcome='DENIED')
            raise PortalError('FORBIDDEN',403)
        return Response(svc.save_employee(request.user,request.data,id))

class EmployeeDuplicatesView(ManagedView):
    @extend_schema(operation_id='EmployeeDuplicatesView_get')
    @extend_schema(responses=response_for('EmployeeDuplicatesView','get'))
    def get(self,request):
        return Response(svc.duplicate_names(str(request.query_params.get('name',''))[:200]))

class EmployeeArchiveView(ManagedView):
    @extend_schema(operation_id='EmployeeArchiveView_post')
    @extend_schema(request=OpenApiTypes.OBJECT,responses=response_for('EmployeeArchiveView','post'))
    def post(self,request,id):
        if set(request.data)-{'replacementManagerId','confirmDetach'} or not isinstance(request.data.get('confirmDetach',False),bool) or not isinstance(request.data.get('replacementManagerId'),(str,type(None))):
            raise PortalError('VALIDATION_ERROR')
        return Response(svc.archive_employee(request.user,id,request.data))

class EmployeeRestoreView(ManagedView):
    @extend_schema(operation_id='EmployeeRestoreView_post', request=OpenApiTypes.OBJECT, responses=response_for('EmployeeRestoreView','post'))
    def post(self,request,id):
        return Response(svc.restore_employee(request.user,id))

class SelfContactView(APIView):
    @extend_schema(operation_id='SelfContactView_patch')
    @extend_schema(request=SelfContact,responses=response_for('SelfContactView','patch'))
    def patch(self,request):
        return Response(svc.self_update(request.user,request.data,SelfContact))

class SelfLocationView(APIView):
    @extend_schema(operation_id='SelfLocationView_patch')
    @extend_schema(request=SelfLocation,responses=response_for('SelfLocationView','patch'))
    def patch(self,request):
        return Response(svc.self_update(request.user,request.data,SelfLocation))

class SelfProfileView(APIView):
    @extend_schema(operation_id='SelfProfileView_patch')
    @extend_schema(request=SelfProfile,responses=response_for('SelfProfileView','patch'))
    def patch(self,request):
        return Response(svc.self_update(request.user,request.data,SelfProfile))

class UnitsView(APIView):
    @extend_schema(operation_id='UnitsView_get', request=OpenApiTypes.OBJECT, responses=response_for('UnitsView','get'))
    def get(self,request):
        return Response(svc.units())
    @extend_schema(operation_id='UnitsView_post')
    @extend_schema(request=UnitInput,responses=response_for('UnitsView','post'))
    def post(self,request):
        if not managed(request.user):
            raise PortalError('FORBIDDEN',403)
        return Response(svc.save_unit(request.user,request.data),status=201)

class UnitView(APIView):
    @extend_schema(operation_id='UnitView_get', request=OpenApiTypes.OBJECT, responses=response_for('UnitView','get'))
    def get(self,request,id):
        item=next((u for u in svc.units() if u['id']==str(id)),None)
        if not item:
            raise PortalError('NOT_FOUND',404)
        return Response({**item,'employees':svc.employees(request.user,{**request.query_params.dict(),'unitId':str(id)})})
    @extend_schema(operation_id='UnitView_patch')
    @extend_schema(request=UnitInput,responses=response_for('UnitView','patch'))
    def patch(self,request,id):
        if not managed(request.user):
            raise PortalError('FORBIDDEN',403)
        return Response(svc.save_unit(request.user,request.data,id))

class OfficesView(APIView):
    @extend_schema(operation_id='OfficesView_get', request=OpenApiTypes.OBJECT, responses=response_for('OfficesView','get'))
    def get(self,request):
        return Response(svc.offices())
    @extend_schema(operation_id='OfficesView_post')
    @extend_schema(request=OfficeInput,responses=response_for('OfficesView','post'))
    def post(self,request):
        if not managed(request.user):
            raise PortalError('FORBIDDEN',403)
        return Response(svc.save_office(request.user,request.data),status=201)

class OfficeView(ManagedView):
    @extend_schema(operation_id='OfficeView_patch')
    @extend_schema(request=OfficeInput,responses=response_for('OfficeView','patch'))
    def patch(self,request,id):
        return Response(svc.save_office(request.user,request.data,id))

class BirthdaysView(APIView):
    @extend_schema(operation_id='BirthdaysView_get', request=OpenApiTypes.OBJECT, responses=response_for('BirthdaysView','get'))
    def get(self,request):
        return Response(svc.birthdays(request.user))

class NewsView(APIView):
    @extend_schema(operation_id='NewsView_get', request=OpenApiTypes.OBJECT, responses=response_for('NewsView','get'))
    def get(self,request):
        return Response(svc.news(request.user,request.query_params))
    @extend_schema(operation_id='NewsView_post')
    @extend_schema(request=NewsInput,responses=response_for('NewsView','post'))
    def post(self,request):
        if not managed(request.user):
            raise PortalError('FORBIDDEN',403)
        return Response(svc.save_news(request.user,request.data),status=201)

class ArticleView(APIView):
    @extend_schema(operation_id='ArticleView_get', request=OpenApiTypes.OBJECT, responses=response_for('ArticleView','get'))
    def get(self,request,id):
        return Response(svc.article(request.user,id))
    @extend_schema(operation_id='ArticleView_patch')
    @extend_schema(request=NewsInput,responses=response_for('ArticleView','patch'))
    def patch(self,request,id):
        if not managed(request.user):
            raise PortalError('FORBIDDEN',403)
        return Response(svc.save_news(request.user,request.data,id))

class ArticleViewedView(APIView):
    @extend_schema(operation_id='ArticleViewedView_post', request=OpenApiTypes.OBJECT, responses=response_for('ArticleViewedView','post'))
    def post(self,request,id):
        return Response(svc.view_news(request.user,id))

class DocumentsView(APIView):
    @extend_schema(operation_id='DocumentsView_get', request=OpenApiTypes.OBJECT, responses=response_for('DocumentsView','get'))
    def get(self,request):
        return Response(svc.documents(request.user,request.query_params))
    @extend_schema(operation_id='DocumentsView_post')
    @extend_schema(request=DocumentInput,responses=response_for('DocumentsView','post'))
    def post(self,request):
        if not managed(request.user):
            raise PortalError('FORBIDDEN',403)
        return Response(svc.save_document(request.user,request.data),status=201)

class DocumentView(APIView):
    @extend_schema(operation_id='DocumentView_get', request=OpenApiTypes.OBJECT, responses=response_for('DocumentView','get'))
    def get(self,request,id):
        return Response(svc.document(request.user,id))
    @extend_schema(operation_id='DocumentView_patch')
    @extend_schema(request=DocumentInput,responses=response_for('DocumentView','patch'))
    def patch(self,request,id):
        if not managed(request.user):
            raise PortalError('FORBIDDEN',403)
        return Response(svc.save_document(request.user,request.data,id))

class VersionsView(ManagedView):
    @extend_schema(operation_id='VersionsView_post')
    @extend_schema(request=VersionInput,responses=response_for('VersionsView','post'))
    def post(self,request,id):
        return Response(svc.add_version(request.user,id,request.data),status=201)

class VersionView(APIView):
    @extend_schema(operation_id='VersionView_get', request=OpenApiTypes.OBJECT, responses=response_for('VersionView','get'))
    def get(self,request,id,version_id):
        document=svc.document(request.user,id)
        version=next((v for v in document['versions'] if v['id']==str(version_id)),None)
        if not version:
            raise PortalError('NOT_FOUND',404)
        model=svc.get_or_error(m.DocumentVersion.objects,pk=version_id)
        # Extracted text is a preview, never substituted for the original file.
        return Response({**version,'document':{k:v for k,v in document.items() if k!='versions'},'outdated':str(version_id)!=document['currentVersionId'],'previewText':model.extracted_text[:2_000_000] if model.file.security_status=='APPROVED' else ''})

class ContentTrashView(ManagedView):
    @extend_schema(operation_id='ContentTrashView_post', request=OpenApiTypes.OBJECT, responses=response_for('ContentTrashView','post'))
    def post(self,request,kind,id):
        return Response(svc.trash(request.user,kind,id))

class TrashView(ManagedView):
    @extend_schema(operation_id='TrashView_get', request=OpenApiTypes.OBJECT, responses=response_for('TrashView','get'))
    def get(self,request):
        return Response(svc.trash_list())

class TrashItemView(ManagedView):
    fresh_required=True
    @extend_schema(operation_id='TrashItemView_delete', request=OpenApiTypes.OBJECT, responses=response_for('TrashItemView','delete'))
    def delete(self,request,kind,id):
        return Response(svc.permanent_delete(request.user,kind,id,request.data.get('confirmation')))

class ContentRestoreView(ManagedView):
    @extend_schema(operation_id='ContentRestoreView_post', request=OpenApiTypes.OBJECT, responses=response_for('ContentRestoreView','post'))
    def post(self,request,kind,id):
        return Response(svc.restore_content(request.user,kind,id))

class SearchView(APIView):
    @extend_schema(operation_id='SearchView_get', request=OpenApiTypes.OBJECT, responses=response_for('SearchView','get'))
    def get(self,request):
        from .security import throttle
        throttle(f'search:{request.user.pk}',120,60)
        return Response(svc.search(request.user,str(request.query_params.get('q',''))[:200]))

class ImportPreviewView(ManagedView):
    @extend_schema(operation_id='ImportPreviewView_post', request=OpenApiTypes.OBJECT, responses=response_for('ImportPreviewView','post'))
    def post(self,request):
        if set(request.data)-{'fileId','mapping'}:
            raise PortalError('VALIDATION_ERROR')
        return Response(preview_import(request.user,request.data.get('fileId'),request.data.get('mapping')))

class ImportCommitView(ManagedView):
    @extend_schema(operation_id='ImportCommitView_post', request=OpenApiTypes.OBJECT, responses=response_for('ImportCommitView','post'))
    def post(self,request,id):
        return Response(commit_import(request.user,id,request.data))

EXPORT_FIELDS=['employeeNumber','displayName','positionTitle','unit','office','managerNumber','extension','mobilePhone','workEmail','birthday','floor','room']
class ExportView(ManagedView):
    @extend_schema(operation_id='ExportView_get', request=OpenApiTypes.OBJECT, responses=response_for('ExportView','get'))
    def get(self,request):
        template=request.query_params.get('template')=='true'
        if template:
            from .employee_templates import template_response
            locale=request.query_params.get('locale',request.user.portal_account.locale)
            response=template_response(locale,request.query_params.get('format','xlsx'))
            audit(request.user,'HR_EXPORT','EMPLOYEE',metadata={'template':True,'exampleRows':2,'locale':locale})
            return response
        data=[]
        if not template:
            for e in svc.employee_queryset().filter(status='ACTIVE').order_by('employee_number'):
                data.append([e.employee_number,e.display_name,e.position_title,e.unit.name,e.office.name,e.manager.employee_number if e.manager else '',e.extension,e.mobile_phone,e.work_email,e.birthday.isoformat() if e.birthday else '',e.floor,e.room])
        if request.query_params.get('format')=='xlsx':
            import openpyxl
            workbook=openpyxl.Workbook()
            sheet=workbook.active
            sheet.title='Employees'
            sheet.append(EXPORT_FIELDS)
            for row in data:
                sheet.append([spreadsheet_safe(v) for v in row])
            out=io.BytesIO()
            workbook.save(out)
            response=HttpResponse(out.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition']='attachment; filename="employees.xlsx"'
        else:
            out=io.StringIO()
            writer=csv.writer(out)
            writer.writerow(EXPORT_FIELDS)
            writer.writerows([[spreadsheet_safe(v) for v in row] for row in data])
            response=HttpResponse('\ufeff'+out.getvalue(),content_type='text/csv; charset=utf-8')
            response['Content-Disposition']='attachment; filename="employee-template.csv"' if template else 'attachment; filename="employees.csv"'
        audit(request.user,'HR_EXPORT','EMPLOYEE',metadata={'template':template,'rows':len(data)})
        return response

class HistoryView(ManagedView):
    @extend_schema(operation_id='HistoryView_get', request=OpenApiTypes.OBJECT, responses=response_for('HistoryView','get'))
    def get(self,request,id):
        return Response([{'action':a.action,'fields':a.changed_fields,'outcome':a.outcome,'createdAt':a.created_at} for a in m.AuditLog.objects.filter(entity_type='EMPLOYEE',entity_id=str(id)).order_by('-created_at')[:100]])

class SystemView(APIView):
    required_roles={'ADMIN'}
    @extend_schema(operation_id='SystemView_get', request=OpenApiTypes.OBJECT, responses=response_for('SystemView','get'))
    def get(self,request):
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return Response({'database':connection.vendor,'demo':not settings.PRODUCTION,'mfaPolicy':settings.REQUIRE_MFA,'storage':settings.STORAGE_DRIVER,'antivirusConfigured':bool(settings.CLAMAV_HOST),'ocrLanguages':settings.OCR_LANGUAGES,'pendingJobs':m.ProcessingJob.objects.filter(status__in=['PENDING','RUNNING']).count(),'failedJobs':m.ProcessingJob.objects.filter(status='ERROR').count(),'quarantinedFiles':m.FileObject.objects.filter(security_status__in=['QUARANTINED','SCANNING','SCAN_ERROR']).count(),'independentPentest':'EXTERNAL_ACCEPTANCE_REQUIRED','productionReadiness':'NOT_VERIFIED'})

class LiveView(APIView):
    permission_classes=[AllowAny]
    authentication_classes=[]
    @extend_schema(operation_id='LiveView_get', request=OpenApiTypes.OBJECT, responses=response_for('LiveView','get'))
    def get(self,request):
        return Response({'status':'alive'})

class ReadyView(LiveView):
    @extend_schema(operation_id='ReadyView_get', request=OpenApiTypes.OBJECT, responses=response_for('ReadyView','get'))
    def get(self,request):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
        except Exception:
            return Response({'status':'not-ready'},status=503)
        if settings.PRODUCTION:
            from .operational import check_runtime
            ready=all(check_runtime().values())
            return Response({'status':'ready' if ready else 'not-ready'},status=200 if ready else 503)
        return Response({'status':'core-ready','filePipeline':'requires-operational-check'})

def spa(request, path=''):
    index=settings.BASE_DIR/'frontend-dist'/'index.html'
    if index.exists():
        return FileResponse(index.open('rb'),content_type='text/html')
    return JsonResponse({'code':'FRONTEND_NOT_BUILT','command':'npm run build:web'},status=503)

def api_not_found(request,path=''):
    return JsonResponse({'code':'NOT_FOUND'},status=404)
