"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api, views
from .api import DatasetVersionViewSet, VersionTaskViewSet

app_name = 'projects'

# reverse for projects:name
_urlpatterns = [
    path('', views.project_list, name='project-index'),
    path('<int:pk>/settings/', views.project_settings, name='project-settings', kwargs={'sub_path': ''}),
    path('<int:pk>/settings/<sub_path>', views.project_settings, name='project-settings-anything'),
]

# reverse for projects:api:name
_api_urlpatterns = [
    # CRUD
    path('', api.ProjectListAPI.as_view(), name='project-list'),
    path('<int:pk>/', api.ProjectAPI.as_view(), name='project-detail'),
    path('counts/', api.ProjectCountsListAPI.as_view(), name='project-counts-list'),
    # Get next task
    path('<int:pk>/next/', api.ProjectNextTaskAPI.as_view(), name='project-next'),
    # Label stream history
    path('<int:pk>/label-stream-history/', api.LabelStreamHistoryAPI.as_view(), name='label-stream-history'),
    # Validate label config in general
    path('validate/', api.LabelConfigValidateAPI.as_view(), name='label-config-validate'),
    # Validate label config for project
    path('<int:pk>/validate/', api.ProjectLabelConfigValidateAPI.as_view(), name='project-label-config-validate'),
    # Project summary
    path('<int:pk>/summary/', api.ProjectSummaryAPI.as_view(), name='project-summary'),
    # Project summary
    path(
        '<int:pk>/summary/reset/',
        api.ProjectSummaryResetAPI.as_view(),
        name='project-summary-reset',
    ),
    # Project import
    path('<int:pk>/imports/<int:import_pk>/', api.ProjectImportAPI.as_view(), name='project-imports'),
    # Project reimport
    path('<int:pk>/reimports/<int:reimport_pk>/', api.ProjectReimportAPI.as_view(), name='project-reimports'),
    # Tasks list for the project: get and destroy
    path('<int:pk>/tasks/', api.ProjectTaskListAPI.as_view(), name='project-tasks-list'),
    # Generate sample task for this project
    path('<int:pk>/sample-task/', api.ProjectSampleTask.as_view(), name='project-sample-task'),
    # List available model versions
    path('<int:pk>/model-versions/', api.ProjectModelVersions.as_view(), name='project-model-versions'),
    # Set active version
    path('<int:pk>/set-active-version/', api.ProjectSetActiveVersionAPI.as_view(), name='project-set-active-version'),
]

_api_urlpatterns_templates = [
    path('', api.TemplateListAPI.as_view(), name='template-list'),
]

router = DefaultRouter()
router.register(r'version-tasks', VersionTaskViewSet, basename='versiontask')

_api_urlpatterns += [
    path('<int:project_pk>/dataset-versions/', DatasetVersionViewSet.as_view({'get': 'list', 'post': 'create'}), name='datasetversion-list'),
    path('<int:project_pk>/dataset-versions/<int:pk>/', DatasetVersionViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}), name='datasetversion-detail'),
    path('<int:project_pk>/dataset-versions/<int:pk>/auto-split/', DatasetVersionViewSet.as_view({'post': 'auto_split'}), name='datasetversion-auto-split'),
    path('<int:project_pk>/dataset-versions/<int:pk>/debug-split/', DatasetVersionViewSet.as_view({'get': 'debug_split'}), name='datasetversion-debug-split'),
    path('<int:project_pk>/dataset-versions/<int:pk>/create-export/', DatasetVersionViewSet.as_view({'post': 'create_export'}), name='datasetversion-create-export'),
    path('<int:project_pk>/dataset-versions/<int:pk>/export-status/<str:export_id>/', DatasetVersionViewSet.as_view({'get': 'export_status'}), name='datasetversion-export-status'),
    path('<int:project_pk>/dataset-versions/<int:pk>/export/', DatasetVersionViewSet.as_view({'get': 'export'}), name='datasetversion-export'),
    path('<int:project_pk>/dataset-versions/<int:pk>/processed-files/', DatasetVersionViewSet.as_view({'get': 'get_processed_files'}), name='datasetversion-processed-files'),
    path('<int:project_pk>/dataset-versions/<int:pk>/retry-processing/', DatasetVersionViewSet.as_view({'post': 'retry_processing'}), name='datasetversion-retry-processing'),
    path('<int:project_pk>/dataset-versions/<int:pk>/download-export/<str:pipeline_name>/<str:commit_id>/', DatasetVersionViewSet.as_view({'get': 'download_export'}), name='datasetversion-download-export'),
    path('<int:project_pk>/dataset-versions/<int:pk>/exports/<str:export_id>/download/', DatasetVersionViewSet.as_view({'get': 'download_export_by_id'}), name='datasetversion-download-export-by-id'),
    path('<int:project_pk>/dataset-versions/available-transforms/', DatasetVersionViewSet.as_view({'get': 'available_transforms'}), name='datasetversion-available-transforms'),
]
_api_urlpatterns += router.urls

urlpatterns = [
    path('projects/', include(_urlpatterns)),
    path('api/projects/', include((_api_urlpatterns, app_name), namespace='api')),
    path('api/templates/', include((_api_urlpatterns_templates, app_name), namespace='api-templates')),
]
