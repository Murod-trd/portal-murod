import datetime as dt
from collections.abc import Mapping
import bleach
from rest_framework import serializers as s
from . import models as m

def html(value):
    return bleach.clean(value, tags=['p','br','strong','em','u','ul','ol','li','h2','h3','blockquote','a'], attributes={'a':['href','title']}, protocols=['https','mailto'], strip=True)

class StrictSerializer(s.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data,Mapping):
            raise s.ValidationError({'non_field_errors':['Expected a JSON object.']})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise s.ValidationError({key:'Unknown or protected field.' for key in unknown})
        return super().to_internal_value(data)

class EmployeeInput(StrictSerializer):
    displayName = s.CharField(source='display_name', max_length=200, min_length=2)
    positionTitle = s.CharField(source='position_title', max_length=200)
    unitId = s.PrimaryKeyRelatedField(source='unit', queryset=m.OrgUnit.objects.filter(active=True))
    officeId = s.PrimaryKeyRelatedField(source='office', queryset=m.Office.objects.filter(active=True))
    managerId = s.PrimaryKeyRelatedField(source='manager', queryset=m.Employee.objects.filter(status='ACTIVE'), allow_null=True, required=False)
    extension = s.CharField(max_length=30, allow_blank=True, required=False)
    workEmail = s.EmailField(source='work_email', allow_blank=True, required=False)
    mobilePhone = s.RegexField(source='mobile_phone', regex=r'^[+\d()\-\s]*$', max_length=40, allow_blank=True, required=False)
    mobileVisible = s.BooleanField(source='mobile_visible', required=False)
    birthday = s.DateField(allow_null=True, required=False)
    birthdayVisible = s.BooleanField(source='birthday_visible', required=False)
    photoFileId = s.PrimaryKeyRelatedField(source='photo', queryset=m.FileObject.objects.filter(purpose='PHOTO',security_status='APPROVED'), allow_null=True, required=False)
    floor = s.CharField(max_length=30, allow_blank=True, required=False)
    room = s.CharField(max_length=40, allow_blank=True, required=False)
    confirmNamesake = s.BooleanField(source='confirm_namesake', required=False, default=False)

class SelfContact(StrictSerializer):
    mobilePhone = s.RegexField(source='mobile_phone', regex=r'^[+\d()\-\s]*$', max_length=40, allow_blank=True, required=False)
    mobileVisible = s.BooleanField(source='mobile_visible', required=False)

class SelfLocation(StrictSerializer):
    floor = s.CharField(max_length=30, allow_blank=True, required=False)
    room = s.CharField(max_length=40, allow_blank=True, required=False)

class SelfProfile(SelfContact,SelfLocation):
    pass

class UnitInput(StrictSerializer):
    name = s.CharField(max_length=200)
    type = s.CharField(max_length=50, required=False, default='DEPARTMENT')
    parentId = s.PrimaryKeyRelatedField(source='parent',queryset=m.OrgUnit.objects.filter(active=True), allow_null=True, required=False)
    headEmployeeId = s.PrimaryKeyRelatedField(source='head',queryset=m.Employee.objects.filter(status='ACTIVE'), allow_null=True, required=False)
    active = s.BooleanField(required=False)

class OfficeInput(StrictSerializer):
    name = s.CharField(max_length=200)
    active = s.BooleanField(required=False)

class Translation(StrictSerializer):
    title = s.CharField(max_length=300, required=False, allow_blank=True)
    summary = s.CharField(max_length=1000, required=False, allow_blank=True)
    body = s.CharField(max_length=100000, required=False, allow_blank=True)
    description = s.CharField(max_length=4000, required=False, allow_blank=True)
    def validate_body(self, value):
        return html(value)

class Translations(s.DictField):
    def to_internal_value(self, data):
        if not isinstance(data,dict) or set(data)-{'uz','ru'}:
            raise s.ValidationError('Only uz/ru translations are supported.')
        result={}
        for locale,value in data.items():
            ser=Translation(data=value)
            ser.is_valid(raise_exception=True)
            result[locale]=ser.validated_data
        return result

class BoundedDateTimeField(s.DateTimeField):
    """Rejects instants outside 2000-2100 (and values that overflow timezone conversion) with a 400."""
    def to_internal_value(self,value):
        try:
            result=super().to_internal_value(value)
            valid=2000<=result.astimezone(dt.timezone.utc).year<=2100
        except (OverflowError,ValueError):
            valid=False
        if not valid:
            raise s.ValidationError('Date is outside the supported range.')
        return result

class NewsInput(StrictSerializer):
    title = s.CharField(max_length=300)
    summary = s.CharField(max_length=1000, allow_blank=True, required=False, default='')
    body = s.CharField(max_length=100000)
    language = s.ChoiceField(choices=['uz','ru'], required=False, default='uz')
    translations = Translations(required=False, default=dict)
    category = s.CharField(max_length=80, required=False, default='NEWS')
    coverFileId = s.PrimaryKeyRelatedField(source='cover', queryset=m.FileObject.objects.filter(purpose='COVER',security_status='APPROVED'), allow_null=True, required=False)
    status = s.ChoiceField(choices=['DRAFT','PUBLISHED'], required=False, default='DRAFT')
    pinned = s.BooleanField(required=False, default=False)
    publishedAt = BoundedDateTimeField(source='published_at',allow_null=True,required=False)
    expiresAt = BoundedDateTimeField(source='expires_at',allow_null=True,required=False)
    def validate(self,attrs):
        if attrs.get('published_at') and attrs.get('expires_at') and attrs['expires_at']<=attrs['published_at']:
            raise s.ValidationError({'expiresAt':'Must be later than publishedAt.'})
        return attrs
    def validate_body(self,value):
        result=html(value)
        if not bleach.clean(result,tags=[],strip=True).strip():
            raise s.ValidationError('Content is empty.')
        return result

class DocumentInput(StrictSerializer):
    title = s.CharField(max_length=300)
    number = s.CharField(max_length=80,allow_blank=True,required=False,default='')
    documentDate = s.DateField(source='document_date',allow_null=True,required=False)
    effectiveDate = s.DateField(source='effective_date',allow_null=True,required=False)
    category = s.CharField(max_length=80)
    description = s.CharField(max_length=4000,allow_blank=True,required=False,default='')
    language = s.ChoiceField(choices=['uz','ru'],required=False,default='uz')
    translations = Translations(required=False,default=dict)
    ownerUnitId = s.PrimaryKeyRelatedField(source='owner_unit',queryset=m.OrgUnit.objects.filter(active=True),allow_null=True,required=False)
    status = s.ChoiceField(choices=['DRAFT','PUBLISHED'],required=False,default='DRAFT')

class VersionInput(StrictSerializer):
    fileId = s.PrimaryKeyRelatedField(source='file',queryset=m.FileObject.objects.filter(purpose='DOCUMENT',security_status='APPROVED'))
    label = s.CharField(max_length=80)
    documentDate = s.DateField(source='document_date',allow_null=True,required=False)
    effectiveDate = s.DateField(source='effective_date',allow_null=True,required=False)
    changeNote = s.CharField(source='change_note',max_length=1000,allow_blank=True,required=False,default='')

class UserInput(StrictSerializer):
    username = s.RegexField(regex=r'^[a-zA-Z0-9._-]{3,64}$',max_length=64)
    name = s.CharField(max_length=200,min_length=2)
    role = s.ChoiceField(choices=['EMPLOYEE','HR','ADMIN'])
    employeeId = s.PrimaryKeyRelatedField(source='employee',queryset=m.Employee.objects.filter(status='ACTIVE'),allow_null=True,required=False)

def validated(cls,data,partial=False):
    serializer=cls(data=data,partial=partial)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data

def employee_data(employee,user):
    privileged=user.portal_account.role in {'HR','ADMIN'}
    birthday=employee.birthday if privileged or employee.birthday_visible else None
    result={
        'id':str(employee.id),'employeeNumber':employee.employee_number,'displayName':employee.display_name,'positionTitle':employee.position_title,
        'unitId':str(employee.unit_id),'unitName':employee.unit.name,'officeId':str(employee.office_id),'officeName':employee.office.name,
        'managerId':str(employee.manager_id) if employee.manager_id else None,'managerName':employee.manager.display_name if employee.manager_id else None,
        'extension':employee.extension,'workEmail':employee.work_email,'mobilePhone':employee.mobile_phone if privileged or employee.mobile_visible or user.portal_account.employee_id==employee.id else None,
        'mobileVisible':employee.mobile_visible,'birthdayVisible':employee.birthday_visible,'birthDay':birthday.day if birthday else None,'birthMonth':birthday.month if birthday else None,
        'photoFileId':str(employee.photo_id) if employee.photo_id else None,'floor':employee.floor,'room':employee.room,'status':employee.status,'updatedAt':employee.updated_at.isoformat(),
    }
    if privileged:
        result['birthday']=birthday.isoformat() if birthday else None
    return result
