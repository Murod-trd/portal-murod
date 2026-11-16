"""Explicit public response contracts, shared by OpenAPI and payload regression tests."""
from rest_framework import serializers as s
from drf_spectacular.utils import OpenApiTypes


def record(name, fields):
    return type(name+'Response', (s.Serializer,), {'__module__': __name__, **fields})


def text(nullable=False, optional=False):
    return s.CharField(allow_blank=True, allow_null=nullable, required=not optional)


def integer(nullable=False, optional=False):
    return s.IntegerField(allow_null=nullable, required=not optional)


def boolean(optional=False):
    return s.BooleanField(required=not optional)


def strings(names, nullable=False, optional=False):
    return {name: text(nullable, optional) for name in names.split()}


def paginated(name, item):
    return record(name+'Page', {'items': item(many=True), 'total': integer(), 'page': integer(), 'limit': integer()})


Error = record('Error', {'code': text(), 'details': s.JSONField(required=False)})
Success = record('Success', {'success': boolean()})
Identity = record('Identity', {'id': text()})
EmployeeSaved = record('EmployeeSaved', {'id': text(), 'employeeNumber': text()})
Duplicate = record('Duplicate', strings('id displayName employeeNumber status'))
DirectReport = record('DirectReport', strings('id displayName positionTitle'))
Employee = record('Employee', {
    **strings('id employeeNumber displayName positionTitle unitId unitName officeId officeName extension workEmail floor room status updatedAt'),
    **strings('managerId managerName mobilePhone photoFileId', nullable=True),
    'mobileVisible': boolean(), 'birthdayVisible': boolean(), 'birthDay': integer(True), 'birthMonth': integer(True),
    'birthday': text(True, True),
})
EmployeeDetail = record('EmployeeDetail', {**Employee().fields, 'directReports': DirectReport(many=True)})
EmployeePage = paginated('Employee', Employee)
Unit = record('Unit', {**strings('id name type'), **strings('parentId headEmployeeId headName', True),
                       'active': boolean(), 'employeeCount': integer(), 'sortOrder': integer()})
UnitDetail = record('UnitDetail', {**Unit().fields, 'employees': EmployeePage()})
Office = record('Office', {**strings('id name'), 'active': boolean()})
Birthday = record('Birthday', {**strings('id displayName positionTitle officeName'), 'photoFileId': text(True),
                              'day': integer(), 'month': integer(), 'days': integer()})
Translation = record('Translation', strings('title summary body description', optional=True))
Translations = record('Translations', {'uz': Translation(required=False), 'ru': Translation(required=False)})
News = record('News', {**strings('id slug title summary category language status updatedAt authorName'),
                      **strings('coverFileId publishedAt expiresAt', True), 'body': text(optional=True),
                      'translations': Translations(), 'pinned': boolean(), 'views': integer()})
NewsPage = paginated('News', News)
Quality = record('OcrQuality', {'engine': text(optional=True), 'ocrPages': integer(optional=True),
                              'meanConfidence': s.FloatField(allow_null=True, required=False),
                              'lowConfidenceLines': integer(optional=True),
                              'rotatedPages': integer(optional=True), 'sparsePages': integer(optional=True),
                              'embeddedImages': integer(optional=True),
                              'rotations': s.ListField(child=integer(), required=False),
                              'profiles': s.ListField(child=text(), required=False)})
Version = record('Version', {**strings('id label changeNote fileId fileName mimeType securityStatus extractionStatus extractionError createdAt'),
                            **strings('documentDate effectiveDate', True), 'fileSize': integer(), 'ocrQuality': Quality(),
                            'outdated': boolean(True), 'currentVersionId': text(True, True), 'snippet': text(optional=True)})
Document = record('Document', {**strings('id title number category description language status updatedAt'),
                              **strings('documentDate effectiveDate ownerUnitId currentVersionId', True),
                              **strings('versionId versionLabel securityStatus mimeType extractionStatus snippet', optional=True),
                              'translations': Translations(), 'fileSize': integer(optional=True), 'outdated': boolean(True),
                              'snippetMatch': s.ListField(child=integer(), required=False)})
DocumentDetail = record('DocumentDetail', {**Document().fields, 'versions': Version(many=True)})
VersionDetail = record('VersionDetail', {**Version().fields, 'document': Document(), 'previewText': text()})
DocumentPage = paginated('Document', Document)
TrashItem = record('TrashItem', {**strings('id title deletedAt'), 'kind': s.ChoiceField(choices=['news', 'documents'])})
Search = record('Search', {'employees': EmployeePage(), 'news': NewsPage(), 'documents': DocumentPage(), 'units': Unit(many=True)})
History = record('History', {**strings('action outcome createdAt'), 'fields': s.ListField(child=text())})
FileState = record('FileState', {**strings('id fileName mimeType securityStatus rejectionCode processingError'),
                                'fileSize': integer(), 'importReady': boolean()})
Csrf = record('Csrf', {'csrfToken': text()})
TokenSuccess = record('TokenSuccess', {'success': boolean(), 'csrfToken': text()})
Login = record('Login', {'success': boolean(), 'mustChangePassword': boolean(), 'mfaEnrolled': boolean(), 'csrfToken': text()})
Role = lambda: s.ChoiceField(choices=['EMPLOYEE', 'HR', 'ADMIN'])
Me = record('Me', {**strings('id name username csrfToken'), 'employeeId': text(True), 'role': Role(),
                  'locale': s.ChoiceField(choices=['uz', 'ru', 'en']), 'mustChangePassword': boolean(),
                  'mfaEnrolled': boolean(), 'mfaRequired': boolean(), 'mfaVerified': boolean(), 'demo': boolean()})
MfaStart = record('MfaStart', {'totpURI': text()})
UserAccount = record('UserAccount', {**strings('id username name'), **strings('employeeId employeeName', True),
                                    'role': Role(), 'active': boolean(), 'mustChangePassword': boolean(), 'mfaEnrolled': boolean()})
Provisioned = record('Provisioned', {'id': text(), 'temporaryPassword': text()})
PasswordReset = record('PasswordReset', {'temporaryPassword': text(), 'mfaPreserved': boolean()})
MfaReset = record('MfaReset', {'temporaryPassword': text(), 'enrolmentRequired': boolean()})
RestoredEmployee = record('RestoredEmployee', {'success': boolean(), 'accountRemainsBlocked': boolean()})
DeletedContent = record('DeletedContent', {'success': boolean(), 'storagePurgeQueued': boolean()})
System = record('System', {**strings('database mfaPolicy storage ocrLanguages independentPentest productionReadiness'),
                          'demo': boolean(), 'antivirusConfigured': boolean(), 'pendingJobs': integer(),
                          'failedJobs': integer(), 'quarantinedFiles': integer()})
Health = record('Health', {'status': text(), 'filePipeline': text(optional=True)})
ImportIssue = record('ImportIssue', {**strings('field code'), 'details': s.JSONField(required=False)})
ImportError = record('ImportError', {'row': integer(), 'errors': ImportIssue(many=True)})
ImportData = record('ImportData', strings('displayName positionTitle unitId officeId managerId extension mobilePhone workEmail birthday floor room', optional=True))
ImportRow = record('ImportRow', {'row': integer(), 'data': ImportData(), 'employeeNumber': text(),
                                **strings('existingId updatedAt', True), 'matches': Duplicate(many=True),
                                'sameNameInFile': boolean(), 'errors': ImportIssue(many=True),
                                'suggestedAction': s.ChoiceField(choices=['UPDATE', 'CHOOSE', 'NEW'])})
ImportPreview = record('ImportPreview', {'id': text(), 'headers': s.ListField(child=text()),
                                        'mapping': s.DictField(child=text()), 'rows': ImportRow(many=True),
                                        'errors': ImportError(many=True), 'total': integer(), 'previewHash': text()})
ImportCommittedItem = record('ImportCommittedItem', {'row': integer(), 'id': text(), 'employeeNumber': text()})
ImportCommit = record('ImportCommit', {'created': integer(), 'updated': integer(), 'skipped': integer(),
                                      'items': ImportCommittedItem(many=True)})

# One entry per public view method. Internal worker protocol is deliberately private.
RESPONSES = {
    'EmployeesView': {'get': (200, EmployeePage), 'post': (201, EmployeeSaved)},
    'EmployeeView': {'get': (200, EmployeeDetail), 'patch': (200, EmployeeSaved)},
    'EmployeeDuplicatesView': {'get': (200, Duplicate(many=True))},
    'EmployeeArchiveView': {'post': (200, Success)}, 'EmployeeRestoreView': {'post': (200, RestoredEmployee)},
    'SelfContactView': {'patch': (200, Success)}, 'SelfLocationView': {'patch': (200, Success)},
    'SelfProfileView': {'patch': (200, Success)},
    'UnitsView': {'get': (200, Unit(many=True)), 'post': (201, Identity)},
    'UnitView': {'get': (200, UnitDetail), 'patch': (200, Identity)},
    'OfficesView': {'get': (200, Office(many=True)), 'post': (201, Identity)},
    'OfficeView': {'patch': (200, Identity)}, 'BirthdaysView': {'get': (200, Birthday(many=True))},
    'NewsView': {'get': (200, NewsPage), 'post': (201, Identity)},
    'ArticleView': {'get': (200, News), 'patch': (200, Identity)},
    'ArticleViewedView': {'post': (200, record('NewsViews', {'views': integer()}))},
    'DocumentsView': {'get': (200, DocumentPage), 'post': (201, Identity)},
    'DocumentView': {'get': (200, DocumentDetail), 'patch': (200, Identity)},
    'VersionsView': {'post': (201, Identity)}, 'VersionView': {'get': (200, VersionDetail)},
    'ContentTrashView': {'post': (200, Success)}, 'TrashView': {'get': (200, TrashItem(many=True))},
    'TrashItemView': {'delete': (200, DeletedContent)}, 'ContentRestoreView': {'post': (200, Success)},
    'SearchView': {'get': (200, Search)}, 'HistoryView': {'get': (200, History(many=True))},
    'ImportPreviewView': {'post': (200, ImportPreview)}, 'ImportCommitView': {'post': (200, ImportCommit)},
    'SystemView': {'get': (200, System)}, 'LiveView': {'get': (200, Health)}, 'ReadyView': {'get': (200, Health)},
    'CsrfView': {'get': (200, Csrf)}, 'LoginView': {'post': (200, Login)},
    'LogoutView': {'post': (200, Success)}, 'MeView': {'get': (200, Me), 'patch': (200, Success)},
    'PasswordView': {'post': (200, TokenSuccess)}, 'MFAStartView': {'post': (200, MfaStart)},
    'MFAVerifyView': {'post': (200, TokenSuccess)},
    'UsersView': {'get': (200, UserAccount(many=True)), 'post': (201, Provisioned)},
    'UserView': {'patch': (200, Success)}, 'ResetPasswordView': {'post': (200, PasswordReset)},
    'ResetMFAView': {'post': (200, MfaReset)},
    'UploadView': {'post': (202, FileState)}, 'FileStateView': {'get': (200, FileState), 'post': (200, FileState)},
    'ReprocessView': {'post': (200, Success)},
}
BINARY_VIEWS = {'FileDownloadView', 'ExportView'}


def response_for(view, method):
    responses = {status: Error for status in [400, 401, 403, 404, 405, 409, 413, 415, 429, 500, 503]}
    if view in BINARY_VIEWS:
        responses[(200, 'application/octet-stream')] = OpenApiTypes.BINARY
    else:
        status, serializer = RESPONSES[view][method]
        responses[status] = serializer
    if view == 'ReadyView':
        responses[503] = Health
    return responses
