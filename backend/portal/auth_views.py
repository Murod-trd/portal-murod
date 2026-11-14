from .contracts import response_for
from .permissions import requires_mfa,requires_password_change
from drf_spectacular.utils import extend_schema, OpenApiTypes
import secrets
import time
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils import timezone
import django_otp
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework import serializers as s
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from .errors import PortalError
from .models import UserAccount, Employee
from .security import throttle, audit, domain_write
from .serializers import StrictSerializer, UserInput, validated
from .services import get_or_error,current_reference

class AccountView(APIView):
    allow_unready=True

class CsrfView(APIView):
    permission_classes=[AllowAny]
    @extend_schema(operation_id='CsrfView_get', request=OpenApiTypes.OBJECT, responses=response_for('CsrfView','get'))
    def get(self,request):
        return Response({'csrfToken':get_token(request)})

class LoginInput(StrictSerializer):
    username=s.CharField(max_length=64)
    password=s.CharField(max_length=128,trim_whitespace=False)

class LoginView(APIView):
    permission_classes=[AllowAny]
    @extend_schema(operation_id='LoginView_post', request=OpenApiTypes.OBJECT, responses=response_for('LoginView','post'))
    def post(self,request):
        data=validated(LoginInput,request.data)
        throttle(f'login-ip:{request.META.get("PORTAL_CLIENT_IP",request.META.get("REMOTE_ADDR", "local"))}',600,300)
        throttle(f'login-user:{data["username"].lower()}',10,300)
        user=authenticate(request=request._request,username=data['username'],password=data['password'])
        if not user or not hasattr(user,'portal_account'):
            raise PortalError('INVALID_CREDENTIALS',401)
        login(request._request,user)
        now=time.time()
        request.session['login_at']=now
        request.session['last_seen_at']=now
        request.session['authenticated_at']=now
        request.session['password_login_complete']=True
        request.session['account_version']=user.portal_account.session_version
        audit(user,'LOGIN','ACCOUNT',user.pk)
        return Response({'success':True,'mustChangePassword':requires_password_change(user.portal_account),'mfaEnrolled':user.totpdevice_set.filter(confirmed=True).exists(),'csrfToken':get_token(request)})

class LogoutView(AccountView):
    @extend_schema(operation_id='LogoutView_post', request=OpenApiTypes.OBJECT, responses=response_for('LogoutView','post'))
    def post(self,request):
        audit(request.user,'LOGOUT','ACCOUNT',request.user.pk)
        logout(request._request)
        return Response({'success':True})

class MeView(AccountView):
    @extend_schema(operation_id='MeView_get', request=OpenApiTypes.OBJECT, responses=response_for('MeView','get'))
    def get(self,request):
        a=request.user.portal_account
        return Response({'id':str(request.user.pk),'name':a.display_name,'username':request.user.username,'role':a.role,'employeeId':str(a.employee_id) if a.employee_id else None,'locale':a.locale,'mustChangePassword':requires_password_change(a),'mfaEnrolled':request.user.totpdevice_set.filter(confirmed=True).exists(),'mfaRequired':requires_mfa(a),'mfaVerified':request.user.is_verified(),'csrfToken':get_token(request),'demo':not __import__('django.conf',fromlist=['settings']).settings.PRODUCTION})
    @extend_schema(operation_id='MeView_patch', request=OpenApiTypes.OBJECT, responses=response_for('MeView','patch'))
    def patch(self,request):
        if set(request.data)-{'locale'} or not isinstance(request.data.get('locale'),str) or request.data['locale'] not in {'uz','ru','en'}:
            raise PortalError('VALIDATION_ERROR')
        account=request.user.portal_account
        account.locale=request.data['locale']
        account.save(update_fields=['locale','updated_at'])
        return Response({'success':True})

class PasswordInput(StrictSerializer):
    currentPassword=s.CharField(max_length=128,trim_whitespace=False)
    newPassword=s.CharField(max_length=128,trim_whitespace=False)

class PasswordView(AccountView):
    @extend_schema(operation_id='PasswordView_post', request=OpenApiTypes.OBJECT, responses=response_for('PasswordView','post'))
    def post(self,request):
        data=validated(PasswordInput,request.data)
        throttle(f'password:{request.user.pk}',6,300)
        if not request.user.check_password(data['currentPassword']):
            raise PortalError('INVALID_CREDENTIALS',400)
        if data['currentPassword']==data['newPassword']:
            raise PortalError('PASSWORD_MUST_CHANGE')
        try:
            validate_password(data['newPassword'],request.user)
        except ValidationError as error:
            raise PortalError('PASSWORD_TOO_WEAK',400,{'newPassword':error.messages})
        with transaction.atomic():
            request.user.set_password(data['newPassword'])
            request.user.save(update_fields=['password'])
            account=request.user.portal_account
            account.must_change_password=False
            account.save(update_fields=['must_change_password','updated_at'])
            update_session_auth_hash(request._request,request.user)
            request.session['authenticated_at']=time.time()
            audit(request.user,'PASSWORD_CHANGED','ACCOUNT',request.user.pk,['password'])
        return Response({'success':True,'csrfToken':get_token(request)})

class MFAStartView(AccountView):
    @extend_schema(operation_id='MFAStartView_post', request=OpenApiTypes.OBJECT, responses=response_for('MFAStartView','post'))
    def post(self,request):
        if requires_password_change(request.user.portal_account):
            raise PortalError('PASSWORD_CHANGE_REQUIRED',403)
        if set(request.data)-{'password'} or not request.user.check_password(str(request.data.get('password',''))):
            raise PortalError('INVALID_CREDENTIALS')
        throttle(f'mfa-start:{request.user.pk}',5,300)
        if request.user.totpdevice_set.filter(confirmed=True).exists():
            raise PortalError('MFA_ALREADY_ENROLLED',409)
        request.user.totpdevice_set.filter(confirmed=False).delete()
        device=TOTPDevice.objects.create(user=request.user,name='Authenticator',confirmed=False,tolerance=1)
        # URI is returned only to the account owner after password re-entry, never logged.
        return Response({'totpURI':device.config_url})

class MFAVerifyView(AccountView):
    @extend_schema(operation_id='MFAVerifyView_post', request=OpenApiTypes.OBJECT, responses=response_for('MFAVerifyView','post'))
    def post(self,request):
        code=str(request.data.get('code',''))
        if set(request.data)-{'code'} or len(code)!=6 or not code.isdigit():
            raise PortalError('INVALID_OTP')
        throttle(f'mfa:{request.user.pk}',10,300)
        device=request.user.totpdevice_set.order_by('-confirmed','-id').first()
        if not device:
            raise PortalError('MFA_NOT_ENROLLED',409)
        verified=False
        with transaction.atomic():
            device=TOTPDevice.objects.select_for_update().get(pk=device.pk)
            verified=device.verify_token(code)
            if verified and not device.confirmed:
                device.confirmed=True
                device.save(update_fields=['confirmed'])
        # Raise after transaction so failed-attempt/replay counters persist.
        if not verified:
            audit(request.user,'MFA_FAILED','ACCOUNT',request.user.pk,outcome='DENIED')
            raise PortalError('INVALID_OTP')
        django_otp.login(request._request,device)
        request.session['authenticated_at']=time.time()
        audit(request.user,'MFA_VERIFIED','ACCOUNT',request.user.pk)
        return Response({'success':True,'csrfToken':get_token(request)})

def provision_account(actor,username,name,role,employee=None,must_change=True,password=None):
    password=password or secrets.token_urlsafe(18)
    with domain_write():
        employee=current_reference(employee,Employee,status='ACTIVE')
        if User.objects.filter(username__iexact=username).exists():
            raise PortalError('USERNAME_EXISTS',409)
        if employee and UserAccount.objects.filter(employee=employee).exists():
            raise PortalError('EMPLOYEE_ALREADY_LINKED',409)
        user=User.objects.create_user(username=username,password=password)
        UserAccount.objects.create(user=user,display_name=name,role=role,employee=employee,must_change_password=must_change)
        audit(actor,'ACCOUNT_CREATED','ACCOUNT',user.pk,['role','employee'])
    return user,password

class UsersView(APIView):
    required_roles={'ADMIN'}
    fresh_required=True
    @extend_schema(operation_id='UsersView_get', request=OpenApiTypes.OBJECT, responses=response_for('UsersView','get'))
    def get(self,request):
        return Response([{'id':str(a.user_id),'username':a.user.username,'name':a.display_name,'role':a.role,'employeeId':str(a.employee_id) if a.employee_id else None,'employeeName':a.employee.display_name if a.employee else None,'active':a.user.is_active,'mustChangePassword':requires_password_change(a),'mfaEnrolled':a.user.totpdevice_set.filter(confirmed=True).exists()} for a in UserAccount.objects.select_related('user','employee').order_by('user__username')])
    @extend_schema(operation_id='UsersView_post', request=OpenApiTypes.OBJECT, responses=response_for('UsersView','post'))
    def post(self,request):
        data=validated(UserInput,request.data)
        user,password=provision_account(request.user,**data)
        return Response({'id':str(user.pk),'temporaryPassword':password},status=201)

class UserUpdateInput(StrictSerializer):
    role=s.ChoiceField(choices=['EMPLOYEE','HR','ADMIN'],required=False)
    active=s.BooleanField(required=False)
    employeeId=s.PrimaryKeyRelatedField(source='employee',queryset=Employee.objects.filter(status='ACTIVE'),allow_null=True,required=False)

class UserView(APIView):
    required_roles={'ADMIN'}
    fresh_required=True
    @extend_schema(operation_id='UserView_patch', request=OpenApiTypes.OBJECT, responses=response_for('UserView','patch'))
    def patch(self,request,id):
        data=validated(UserUpdateInput,request.data,partial=True)
        with domain_write():
            if 'employee' in data:
                data['employee']=current_reference(data['employee'],Employee,status='ACTIVE')
            a=get_or_error(UserAccount.objects.select_for_update().select_related('user'),user_id=id)
            if a.role=='ADMIN' and (data.get('role','ADMIN')!='ADMIN' or data.get('active') is False) and not UserAccount.objects.filter(role='ADMIN',user__is_active=True).exclude(pk=a.pk).exists():
                raise PortalError('LAST_ADMIN',409)
            if data.get('active') and a.employee_id and a.employee.status!='ACTIVE':
                raise PortalError('EMPLOYEE_ARCHIVED',409)
            for key in ['role','employee']:
                if key in data:
                    setattr(a,key,data[key])
            if 'active' in data:
                a.user.is_active=data['active']
                a.user.save(update_fields=['is_active'])
                a.blocked_at=None if data['active'] else timezone.now()
            a.save()
            a.session_version+=1
            a.save(update_fields=['session_version','updated_at'])
            audit(request.user,'ACCOUNT_UPDATED','ACCOUNT',id,data.keys())
        return Response({'success':True})

class ResetPasswordView(APIView):
    required_roles={'ADMIN'}
    fresh_required=True
    @extend_schema(operation_id='ResetPasswordView_post', request=OpenApiTypes.OBJECT, responses=response_for('ResetPasswordView','post'))
    def post(self,request,id):
        a=get_or_error(UserAccount.objects.select_related('user'),user_id=id)
        password=secrets.token_urlsafe(18)
        with transaction.atomic():
            a.user.set_password(password)
            a.user.save(update_fields=['password'])
            a.must_change_password=True
            a.save(update_fields=['must_change_password','updated_at'])
            audit(request.user,'PASSWORD_RESET','ACCOUNT',id,['password'])
        return Response({'temporaryPassword':password,'mfaPreserved':True})

class ResetMFAView(APIView):
    required_roles={'ADMIN'}
    fresh_required=True
    @extend_schema(operation_id='ResetMFAView_post', request=OpenApiTypes.OBJECT, responses=response_for('ResetMFAView','post'))
    def post(self,request,id):
        a=get_or_error(UserAccount.objects.select_related('user'),user_id=id)
        if request.data.get('confirmation')!=a.user.username:
            raise PortalError('CONFIRMATION_REQUIRED')
        password=secrets.token_urlsafe(18)
        with transaction.atomic():
            a.user.totpdevice_set.all().delete()
            a.user.set_password(password)
            a.user.save(update_fields=['password'])
            a.must_change_password=True
            a.save(update_fields=['must_change_password','updated_at'])
            audit(request.user,'MFA_RESET','ACCOUNT',id,['mfa','password'])
        return Response({'temporaryPassword':password,'enrolmentRequired':True})
