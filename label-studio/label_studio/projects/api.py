"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license.
"""
import logging
import os
import pathlib

import drf_yasg.openapi as openapi
from core.filters import ListFilter
from core.label_config import config_essential_data_has_changed
from core.mixins import GetParentObjectMixin
from core.permissions import ViewClassPermission, all_permissions
from core.redis import start_job_async_or_sync
from core.utils.common import paginator, paginator_help, temporary_disconnect_all_signals
from core.utils.exceptions import LabelStudioDatabaseException, ProjectExistException
from core.utils.io import find_dir, find_file, read_yaml
from data_manager.functions import filters_ordering_selected_items_exist, get_prepared_queryset
from django.conf import settings
from django.db import IntegrityError
from django.db.models import F
from django.http import Http404
from django.utils.decorators import method_decorator
from django_filters import CharFilter, FilterSet
from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg.utils import swagger_auto_schema
from label_studio_sdk.label_interface.interface import LabelInterface
from ml.serializers import MLBackendSerializer
from projects.functions.next_task import get_next_task
from projects.functions.stream_history import get_label_stream_history
from projects.functions.utils import recalculate_created_annotations_and_labels_from_scratch
from projects.models import (
    Project,
    ProjectImport,
    ProjectManager,
    ProjectReimport,
    ProjectSummary,
    DatasetVersion,
    VersionTask,
    ProcessedTask,
    DatasetExport,
)
from projects.serializers import (
    GetFieldsSerializer,
    ProjectCountsSerializer,
    ProjectImportSerializer,
    ProjectLabelConfigSerializer,
    ProjectModelVersionExtendedSerializer,
    ProjectReimportSerializer,
    ProjectSerializer,
    ProjectSummarySerializer,
    DatasetVersionSerializer,
    VersionTaskSerializer,
)
from rest_framework import filters, generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from django.http import FileResponse
from django.utils import timezone
from data_export.models import DataExport
from data_export.serializers import ExportDataSerializer
from data_transforms.preprocessing import apply_preprocessing, transform_annotations
from data_transforms.augmentation import apply_augmentation
from PIL import Image
import io
from rest_framework.exceptions import ValidationError as RestValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import exception_handler
from tasks.models import Task
from tasks.serializers import (
    NextTaskSerializer,
    TaskSerializer,
    TaskSimpleSerializer,
    TaskWithAnnotationsAndPredictionsAndDraftsSerializer,
)
from webhooks.models import WebhookAction
from webhooks.utils import api_webhook, api_webhook_for_delete, emit_webhooks_for_instance

from label_studio.core.utils.common import load_func

logger = logging.getLogger(__name__)

ProjectImportPermission = load_func(settings.PROJECT_IMPORT_PERMISSION)

_result_schema = openapi.Schema(
    title='Labeling result',
    description='Labeling result (choices, labels, bounding boxes, etc.)',
    type=openapi.TYPE_OBJECT,
    properties={
        'from_name': openapi.Schema(
            title='from_name',
            description='The name of the labeling tag from the project config',
            type=openapi.TYPE_STRING,
        ),
        'to_name': openapi.Schema(
            title='to_name',
            description='The name of the labeling tag from the project config',
            type=openapi.TYPE_STRING,
        ),
        'value': openapi.Schema(
            title='value',
            description='Labeling result value. Format depends on chosen ML backend',
            type=openapi.TYPE_OBJECT,
        ),
    },
    example={'from_name': 'image_class', 'to_name': 'image', 'value': {'labels': ['Cat']}},
)

_task_data_schema = openapi.Schema(
    title='Task data',
    description='Task data',
    type=openapi.TYPE_OBJECT,
    example={'id': 1, 'my_image_url': '/static/samples/kittens.jpg'},
)

_project_schema = openapi.Schema(
    title='Project',
    description='Project',
    type=openapi.TYPE_OBJECT,
    properties={
        'title': openapi.Schema(
            title='title',
            description='Project title',
            type=openapi.TYPE_STRING,
            example='My project',
        ),
        'description': openapi.Schema(
            title='description',
            description='Project description',
            type=openapi.TYPE_STRING,
            example='My first project',
        ),
        'label_config': openapi.Schema(
            title='label_config',
            description='Label config in XML format',
            type=openapi.TYPE_STRING,
            example='<View>[...]</View>',
        ),
        'expert_instruction': openapi.Schema(
            title='expert_instruction',
            description='Labeling instructions to show to the user',
            type=openapi.TYPE_STRING,
            example='Label all cats',
        ),
        'show_instruction': openapi.Schema(
            title='show_instruction',
            description='Show labeling instructions',
            type=openapi.TYPE_BOOLEAN,
        ),
        'show_skip_button': openapi.Schema(
            title='show_skip_button',
            description='Show skip button',
            type=openapi.TYPE_BOOLEAN,
        ),
        'enable_empty_annotation': openapi.Schema(
            title='enable_empty_annotation',
            description='Allow empty annotations',
            type=openapi.TYPE_BOOLEAN,
        ),
        'show_annotation_history': openapi.Schema(
            title='show_annotation_history',
            description='Show annotation history',
            type=openapi.TYPE_BOOLEAN,
        ),
        'reveal_preannotations_interactively': openapi.Schema(
            title='reveal_preannotations_interactively',
            description='Reveal preannotations interactively. If set to True, predictions will be shown to the user only after selecting the area of interest',
            type=openapi.TYPE_BOOLEAN,
        ),
        'show_collab_predictions': openapi.Schema(
            title='show_collab_predictions',
            description='Show predictions to annotators',
            type=openapi.TYPE_BOOLEAN,
        ),
        'maximum_annotations': openapi.Schema(
            title='maximum_annotations',
            description='Maximum annotations per task',
            type=openapi.TYPE_INTEGER,
        ),
        'color': openapi.Schema(
            title='color',
            description='Project color in HEX format',
            type=openapi.TYPE_STRING,
            default='#FFFFFF',
        ),
        'control_weights': openapi.Schema(
            title='control_weights',
            description='Dict of weights for each control tag in metric calculation. Each control tag (e.g. label or choice) will '
            'have its own key in control weight dict with weight for each label and overall weight. '
            'For example, if a bounding box annotation with a control tag named my_bbox should be included with 0.33 weight in agreement calculation, '
            'and the first label Car should be twice as important as Airplane, then you need to specify: '
            "{'my_bbox': {'type': 'RectangleLabels', 'labels': {'Car': 1.0, 'Airplane': 0.5}, 'overall': 0.33}",
            type=openapi.TYPE_OBJECT,
            example={
                'my_bbox': {'type': 'RectangleLabels', 'labels': {'Car': 1.0, 'Airplaine': 0.5}, 'overall': 0.33}
            },
        ),
    },
)


class ProjectListPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'page_size'


class ProjectFilterSet(FilterSet):
    ids = ListFilter(field_name='id', lookup_expr='in')
    title = CharFilter(field_name='title', lookup_expr='icontains')


@method_decorator(
    name='get',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_sdk_group_name='projects',
        x_fern_sdk_method_name='list',
        x_fern_audiences=['public'],
        x_fern_pagination={
            'offset': '$request.page',
            'results': '$response.results',
        },
        operation_summary='List your projects',
        operation_description="""
    Return a list of the projects that you've created.

    To perform most tasks with the Label Studio API, you must specify the project ID, sometimes referred to as the `pk`.
    To retrieve a list of your Label Studio projects, update the following command to match your own environment.
    Replace the domain name, port, and authorization token, then run the following from the command line:
    ```bash
    curl -X GET {}/api/projects/ -H 'Authorization: Token abc123'
    ```
    """.format(
            settings.HOSTNAME or 'https://localhost:8080'
        ),
    ),
)
@method_decorator(
    name='post',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        operation_summary='Create new project',
        x_fern_sdk_group_name='projects',
        x_fern_sdk_method_name='create',
        x_fern_audiences=['public'],
        operation_description="""
    Create a project and set up the labeling interface in Label Studio using the API.

    ```bash
    curl -H Content-Type:application/json -H 'Authorization: Token abc123' -X POST '{}/api/projects' \
    --data '{{"title": "My project", "label_config": "<View></View>"}}'
    ```
    """.format(
            settings.HOSTNAME or 'https://localhost:8080'
        ),
        request_body=_project_schema,
    ),
)
class ProjectListAPI(generics.ListCreateAPIView):
    parser_classes = (JSONParser, FormParser, MultiPartParser)
    serializer_class = ProjectSerializer
    filter_backends = [filters.OrderingFilter, DjangoFilterBackend]
    filterset_class = ProjectFilterSet
    permission_required = ViewClassPermission(
        GET=all_permissions.projects_view,
        POST=all_permissions.projects_create,
    )
    pagination_class = ProjectListPagination

    def get_queryset(self):
        serializer = GetFieldsSerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data.get('include')
        filter = serializer.validated_data.get('filter')
        projects = Project.objects.filter(organization=self.request.user.active_organization).order_by(
            F('pinned_at').desc(nulls_last=True), '-created_at'
        )
        if filter in ['pinned_only', 'exclude_pinned']:
            projects = projects.filter(pinned_at__isnull=filter == 'exclude_pinned')
        return ProjectManager.with_counts_annotate(projects, fields=fields).prefetch_related('members', 'created_by')

    def get_serializer_context(self):
        context = super(ProjectListAPI, self).get_serializer_context()
        context['created_by'] = self.request.user
        return context

    def perform_create(self, ser):
        try:
            ser.save(organization=self.request.user.active_organization)
        except IntegrityError as e:
            if str(e) == 'UNIQUE constraint failed: project.title, project.created_by_id':
                raise ProjectExistException(
                    'Project with the same name already exists: {}'.format(ser.validated_data.get('title', ''))
                )
            raise LabelStudioDatabaseException('Database error during project creation. Try again.')

    def get(self, request, *args, **kwargs):
        return super(ProjectListAPI, self).get(request, *args, **kwargs)

    @api_webhook(WebhookAction.PROJECT_CREATED)
    def post(self, request, *args, **kwargs):
        return super(ProjectListAPI, self).post(request, *args, **kwargs)


@method_decorator(
    name='get',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_sdk_group_name='projects',
        x_fern_sdk_method_name='counts',
        x_fern_audiences=['public'],
        x_fern_pagination={
            'offset': '$request.page',
            'results': '$response.results',
        },
        operation_summary="List project's counts",
        operation_description='Returns a list of projects with their counts. For example, task_number which is the total task number in project',
    ),
)
class ProjectCountsListAPI(generics.ListAPIView):
    serializer_class = ProjectCountsSerializer
    filterset_class = ProjectFilterSet
    permission_required = ViewClassPermission(
        GET=all_permissions.projects_view,
    )
    pagination_class = ProjectListPagination

    def get_queryset(self):
        serializer = GetFieldsSerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data.get('include')
        return Project.objects.with_counts(fields=fields).filter(organization=self.request.user.active_organization)


@method_decorator(
    name='get',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_sdk_group_name='projects',
        x_fern_sdk_method_name='get',
        x_fern_audiences=['public'],
        operation_summary='Get project by ID',
        operation_description='Retrieve information about a project by project ID.',
        responses={
            '200': openapi.Response(
                description='Project information',
                schema=ProjectSerializer,
                examples={
                    'application/json': {
                        'id': 1,
                        'title': 'My project',
                        'description': 'My first project',
                        'label_config': '<View>[...]</View>',
                        'expert_instruction': 'Label all cats',
                        'show_instruction': True,
                        'show_skip_button': True,
                        'enable_empty_annotation': True,
                        'show_annotation_history': True,
                        'organization': 1,
                        'color': '#FF0000',
                        'maximum_annotations': 1,
                        'is_published': True,
                        'model_version': '1.0.0',
                        'is_draft': False,
                        'created_by': {
                            'id': 1,
                            'first_name': 'Jo',
                            'last_name': 'Doe',
                            'email': 'manager@humansignal.com',
                        },
                        'created_at': '2023-08-24T14:15:22Z',
                        'min_annotations_to_start_training': 0,
                        'start_training_on_annotation_update': True,
                        'show_collab_predictions': True,
                        'num_tasks_with_annotations': 10,
                        'task_number': 100,
                        'useful_annotation_number': 10,
                        'ground_truth_number': 5,
                        'skipped_annotations_number': 0,
                        'total_annotations_number': 10,
                        'total_predictions_number': 0,
                        'sampling': 'Sequential sampling',
                        'show_ground_truth_first': True,
                        'show_overlap_first': True,
                        'overlap_cohort_percentage': 100,
                        'task_data_login': 'user',
                        'task_data_password': 'secret',
                        'control_weights': {},
                        'parsed_label_config': '{"tag": {...}}',
                        'evaluate_predictions_automatically': False,
                        'config_has_control_tags': True,
                        'skip_queue': 'REQUEUE_FOR_ME',
                        'reveal_preannotations_interactively': True,
                        'pinned_at': '2023-08-24T14:15:22Z',
                        'finished_task_number': 10,
                        'queue_total': 10,
                        'queue_done': 100,
                    }
                },
            )
        },
    ),
)
@method_decorator(
    name='delete',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_sdk_group_name='projects',
        x_fern_sdk_method_name='delete',
        x_fern_audiences=['public'],
        operation_summary='Delete project',
        operation_description='Delete a project by specified project ID.',
    ),
)
@method_decorator(
    name='patch',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_sdk_group_name='projects',
        x_fern_sdk_method_name='update',
        x_fern_audiences=['public'],
        operation_summary='Update project',
        operation_description='Update the project settings for a specific project.',
        request_body=_project_schema,
    ),
)
class ProjectAPI(generics.RetrieveUpdateDestroyAPIView):
    parser_classes = (JSONParser, FormParser, MultiPartParser)
    queryset = Project.objects.with_counts()
    permission_required = ViewClassPermission(
        GET=all_permissions.projects_view,
        DELETE=all_permissions.projects_delete,
        PATCH=all_permissions.projects_change,
        PUT=all_permissions.projects_change,
        POST=all_permissions.projects_create,
    )
    serializer_class = ProjectSerializer

    redirect_route = 'projects:project-detail'
    redirect_kwarg = 'pk'

    def get_queryset(self):
        serializer = GetFieldsSerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data.get('include')
        return Project.objects.with_counts(fields=fields).filter(organization=self.request.user.active_organization)

    def get(self, request, *args, **kwargs):
        return super(ProjectAPI, self).get(request, *args, **kwargs)

    @api_webhook_for_delete(WebhookAction.PROJECT_DELETED)
    def delete(self, request, *args, **kwargs):
        return super(ProjectAPI, self).delete(request, *args, **kwargs)

    @api_webhook(WebhookAction.PROJECT_UPDATED)
    def patch(self, request, *args, **kwargs):
        project = self.get_object()
        label_config = self.request.data.get('label_config')

        # config changes can break view, so we need to reset them
        if label_config:
            try:
                _has_changes = config_essential_data_has_changed(label_config, project.label_config)
            except KeyError:
                pass

        return super(ProjectAPI, self).patch(request, *args, **kwargs)

    def perform_destroy(self, instance):
        # we don't need to relaculate counters if we delete whole project
        with temporary_disconnect_all_signals():
            instance.delete()

    @swagger_auto_schema(auto_schema=None)
    @api_webhook(WebhookAction.PROJECT_UPDATED)
    def put(self, request, *args, **kwargs):
        return super(ProjectAPI, self).put(request, *args, **kwargs)


@method_decorator(
    name='get',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        operation_summary='Get next task to label',
        x_fern_sdk_group_name='projects',
        x_fern_sdk_method_name='next_task',
        x_fern_audiences=['public'],
        operation_description="""
    Get the next task for labeling. If you enable Machine Learning in
    your project, the response might include a "predictions"
    field. It contains a machine learning prediction result for
    this task.
    """,
        responses={200: TaskWithAnnotationsAndPredictionsAndDraftsSerializer()},
    ),
)  # leaving this method decorator info in case we put it back in swagger API docs
class ProjectNextTaskAPI(generics.RetrieveAPIView):
    permission_required = all_permissions.tasks_view
    serializer_class = TaskWithAnnotationsAndPredictionsAndDraftsSerializer  # using it for swagger API docs
    queryset = Project.objects.all()
    swagger_schema = None  # this endpoint doesn't need to be in swagger API docs

    def get(self, request, *args, **kwargs):
        project = self.get_object()
        dm_queue = filters_ordering_selected_items_exist(request.data)
        prepared_tasks = get_prepared_queryset(request, project)

        next_task, queue_info = get_next_task(request.user, prepared_tasks, project, dm_queue)

        if next_task is None:
            raise NotFound(
                f'There are still some tasks to complete for the user={request.user}, '
                f'but they seem to be locked by another user.'
            )

        # serialize task
        context = {'request': request, 'project': project, 'resolve_uri': True, 'annotations': False}
        serializer = NextTaskSerializer(next_task, context=context)
        response = serializer.data

        response['queue'] = queue_info
        return Response(response)


class LabelStreamHistoryAPI(generics.RetrieveAPIView):
    permission_required = all_permissions.tasks_view
    queryset = Project.objects.all()
    swagger_schema = None  # this endpoint doesn't need to be in swagger API docs

    def get(self, request, *args, **kwargs):
        project = self.get_object()

        history = get_label_stream_history(request.user, project)

        return Response(history)


@method_decorator(
    name='post',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_audiences=['internal'],
        operation_summary='Validate label config',
        operation_description='Validate an arbitrary labeling configuration.',
        responses={204: 'Validation success'},
        request_body=ProjectLabelConfigSerializer,
    ),
)
class LabelConfigValidateAPI(generics.CreateAPIView):
    parser_classes = (JSONParser, FormParser, MultiPartParser)
    permission_classes = (AllowAny,)
    serializer_class = ProjectLabelConfigSerializer

    def post(self, request, *args, **kwargs):
        return super(LabelConfigValidateAPI, self).post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except RestValidationError as exc:
            context = self.get_exception_handler_context()
            response = exception_handler(exc, context)
            response = self.finalize_response(request, response)
            return response

        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(
    name='post',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        operation_id='api_projects_validate_label_config',
        operation_summary='Validate project label config',
        x_fern_sdk_group_name='projects',
        x_fern_sdk_method_name='validate_config',
        x_fern_audiences=['public'],
        operation_description="""
        Determine whether the label configuration for a specific project is valid.
        """,
        manual_parameters=[
            openapi.Parameter(
                name='id',
                type=openapi.TYPE_INTEGER,
                in_=openapi.IN_PATH,
                description='A unique integer value identifying this project.',
            ),
        ],
        request_body=ProjectLabelConfigSerializer,
    ),
)
class ProjectLabelConfigValidateAPI(generics.RetrieveAPIView):
    """Validate label config"""

    parser_classes = (JSONParser, FormParser, MultiPartParser)
    serializer_class = ProjectLabelConfigSerializer
    permission_required = all_permissions.projects_change
    queryset = Project.objects.all()

    def post(self, request, *args, **kwargs):
        project = self.get_object()
        label_config = self.request.data.get('label_config')
        if not label_config:
            raise RestValidationError('Label config is not set or is empty')

        # check new config includes meaningful changes
        has_changed = config_essential_data_has_changed(label_config, project.label_config)
        project.validate_config(label_config, strict=True)
        return Response({'config_essential_data_has_changed': has_changed}, status=status.HTTP_200_OK)

    @swagger_auto_schema(auto_schema=None)
    def get(self, request, *args, **kwargs):
        return super(ProjectLabelConfigValidateAPI, self).get(request, *args, **kwargs)


class ProjectSummaryAPI(generics.RetrieveAPIView):
    parser_classes = (JSONParser,)
    serializer_class = ProjectSummarySerializer
    permission_required = all_permissions.projects_view
    queryset = ProjectSummary.objects.all()

    @swagger_auto_schema(auto_schema=None)
    def get(self, *args, **kwargs):
        return super(ProjectSummaryAPI, self).get(*args, **kwargs)


class ProjectSummaryResetAPI(GetParentObjectMixin, generics.CreateAPIView):
    """This API is useful when we need to reset project.summary.created_labels and created_labels_drafts
    and recalculate them from scratch. It's hard to correctly follow all changes in annotation region
    labels and these fields aren't calculated properly after some time. Label config changes are not allowed
    when these changes touch any labels from these created_labels* dictionaries.
    """

    parser_classes = (JSONParser,)
    parent_queryset = Project.objects.all()
    permission_required = ViewClassPermission(
        POST=all_permissions.projects_change,
    )

    @swagger_auto_schema(auto_schema=None)
    def post(self, *args, **kwargs):
        project = self.parent_object
        summary = project.summary
        start_job_async_or_sync(
            recalculate_created_annotations_and_labels_from_scratch,
            project,
            summary,
            organization_id=self.request.user.active_organization.id,
        )
        return Response(status=status.HTTP_200_OK)


@method_decorator(
    name='get',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_sdk_group_name='tasks',
        x_fern_sdk_method_name='create_many_status',
        x_fern_audiences=['public'],
        operation_summary='Get project import info',
        operation_description='Return data related to async project import operation',
        manual_parameters=[
            openapi.Parameter(
                name='id',
                type=openapi.TYPE_INTEGER,
                in_=openapi.IN_PATH,
                description='A unique integer value identifying this project import.',
            ),
        ],
    ),
)
class ProjectImportAPI(generics.RetrieveAPIView):
    permission_required = all_permissions.projects_change
    permission_classes = api_settings.DEFAULT_PERMISSION_CLASSES + [ProjectImportPermission]
    parser_classes = (JSONParser,)
    serializer_class = ProjectImportSerializer
    queryset = ProjectImport.objects.all()
    lookup_url_kwarg = 'import_pk'


@method_decorator(
    name='get',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_audiences=['internal'],
        operation_summary='Get project reimport info',
        operation_description='Return data related to async project reimport operation',
        manual_parameters=[
            openapi.Parameter(
                name='id',
                type=openapi.TYPE_INTEGER,
                in_=openapi.IN_PATH,
                description='A unique integer value identifying this project reimport.',
            ),
        ],
    ),
)
class ProjectReimportAPI(generics.RetrieveAPIView):
    permission_required = all_permissions.projects_change
    permission_classes = api_settings.DEFAULT_PERMISSION_CLASSES + [ProjectImportPermission]
    parser_classes = (JSONParser,)
    serializer_class = ProjectReimportSerializer
    queryset = ProjectReimport.objects.all()
    lookup_url_kwarg = 'reimport_pk'


@method_decorator(
    name='delete',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_sdk_group_name='projects',
        x_fern_sdk_method_name='delete_all_tasks',
        x_fern_audiences=['public'],
        operation_summary='Delete all tasks',
        operation_description='Delete all tasks from a specific project.',
        manual_parameters=[
            openapi.Parameter(
                name='id',
                type=openapi.TYPE_INTEGER,
                in_=openapi.IN_PATH,
                description='A unique integer value identifying this project.',
            ),
        ],
        responses={204: 'Tasks deleted'},
    ),
)
@method_decorator(
    name='get',
    decorator=swagger_auto_schema(
        tags=['Projects'],
        x_fern_audiences=['internal'],  # TODO: deprecate this endpoint in favor of tasks:tasks-list
        operation_summary='List project tasks',
        operation_description="""
            Retrieve a paginated list of tasks for a specific project. For example, use the following cURL command:
            ```bash
            curl -X GET {}/api/projects/{{id}}/tasks/?page=1&page_size=10 -H 'Authorization: Token abc123'
            ```
        """.format(
            settings.HOSTNAME or 'https://localhost:8080'
        ),
        manual_parameters=[
            openapi.Parameter(
                name='id',
                type=openapi.TYPE_INTEGER,
                in_=openapi.IN_PATH,
                description='A unique integer value identifying this project.',
            ),
        ]
        + paginator_help('tasks', 'Projects')['manual_parameters'],
    ),
)
class ProjectTaskListAPI(GetParentObjectMixin, generics.ListCreateAPIView, generics.DestroyAPIView):
    parser_classes = (JSONParser, FormParser)
    queryset = Task.objects.all()
    parent_queryset = Project.objects.all()
    permission_required = ViewClassPermission(
        GET=all_permissions.tasks_view,
        POST=all_permissions.tasks_change,
        DELETE=all_permissions.tasks_delete,
    )
    serializer_class = TaskSerializer
    redirect_route = 'projects:project-settings'
    redirect_kwarg = 'pk'

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return TaskSimpleSerializer
        else:
            return TaskSerializer

    def filter_queryset(self, queryset):
        project = generics.get_object_or_404(Project.objects.for_user(self.request.user), pk=self.kwargs.get('pk', 0))
        # ordering is deprecated here
        tasks = Task.objects.filter(project=project).order_by('-updated_at')
        page = paginator(tasks, self.request)
        if page:
            return page
        else:
            raise Http404

    def delete(self, request, *args, **kwargs):
        project = generics.get_object_or_404(Project.objects.for_user(self.request.user), pk=self.kwargs['pk'])
        task_ids = list(Task.objects.filter(project=project).values('id'))
        Task.delete_tasks_without_signals(Task.objects.filter(project=project))
        logger.info(f'calling reset project_id={project.id} ProjectTaskListAPI.delete()')
        project.summary.reset()
        emit_webhooks_for_instance(request.user.active_organization, None, WebhookAction.TASKS_DELETED, task_ids)
        return Response(status=204)

    def get(self, *args, **kwargs):
        return super(ProjectTaskListAPI, self).get(*args, **kwargs)

    @swagger_auto_schema(auto_schema=None)
    def post(self, *args, **kwargs):
        return super(ProjectTaskListAPI, self).post(*args, **kwargs)

    def get_serializer_context(self):
        context = super(ProjectTaskListAPI, self).get_serializer_context()
        context['project'] = self.parent_object
        return context

    def perform_create(self, serializer):
        project = self.parent_object
        instance = serializer.save(project=project)
        emit_webhooks_for_instance(
            self.request.user.active_organization, project, WebhookAction.TASKS_CREATED, [instance]
        )
        return instance


def read_templates_and_groups():
    annotation_templates_dir = find_dir('annotation_templates')
    configs = []
    for config_file in pathlib.Path(annotation_templates_dir).glob('**/*.yml'):
        config = read_yaml(config_file)
        if settings.VERSION_EDITION == 'Community':
            if settings.VERSION_EDITION.lower() != config.get('type', 'community'):
                continue
        if config.get('image', '').startswith('/static') and settings.HOSTNAME:
            # if hostname set manually, create full image urls
            config['image'] = settings.HOSTNAME + config['image']
        configs.append(config)
    template_groups_file = find_file(os.path.join('annotation_templates', 'groups.txt'))
    with open(template_groups_file, encoding='utf-8') as f:
        groups = f.read().splitlines()
    logger.debug(f'{len(configs)} templates found.')
    return {'templates': configs, 'groups': groups}


class TemplateListAPI(generics.ListAPIView):
    parser_classes = (JSONParser, FormParser, MultiPartParser)
    permission_required = all_permissions.projects_view
    swagger_schema = None
    # load this once in memory for performance
    templates_and_groups = read_templates_and_groups()

    def list(self, request, *args, **kwargs):
        return Response(self.templates_and_groups)


class ProjectSampleTask(generics.RetrieveAPIView):
    parser_classes = (JSONParser,)
    queryset = Project.objects.all()
    permission_required = all_permissions.projects_view
    serializer_class = ProjectSerializer
    swagger_schema = None

    def post(self, request, *args, **kwargs):
        label_config = self.request.data.get('label_config')
        include_annotation_and_prediction = self.request.data.get('include_annotation_and_prediction', False)

        if not label_config:
            raise RestValidationError('Label config is not set or is empty')

        project = self.get_object()

        if include_annotation_and_prediction:
            try:
                label_interface = LabelInterface(label_config)
                complete_task = label_interface.generate_complete_sample_task(raise_on_failure=True)
                # set the annotation's user id to the current user instead of -1
                user_id = request.user.id
                for annotation in complete_task['annotations']:
                    annotation['completed_by'] = user_id
                return Response({'sample_task': complete_task}, status=200)
            except Exception as e:
                logger.error(
                    f'Error generating enhanced sample task, falling back to original method: {str(e)}. Label config: {label_config}'
                )
                # Fallback to project.get_sample_task if LabelInterface.generate_complete_sample_task failed
                return Response({'sample_task': project.get_sample_task(label_config)}, status=200)
        else:
            # Use the simple sample task generation method
            return Response({'sample_task': project.get_sample_task(label_config)}, status=200)


class ProjectModelVersions(generics.RetrieveAPIView):
    parser_classes = (JSONParser,)
    swagger_schema = None
    permission_required = all_permissions.projects_view
    queryset = Project.objects.all()

    def get(self, request, *args, **kwargs):
        # TODO make sure "extended" is the right word and is
        # consistent with other APIs we've got
        extended = self.request.query_params.get('extended', False)
        include_live_models = self.request.query_params.get('include_live_models', False)
        project = self.get_object()
        data = project.get_model_versions(with_counters=True, extended=extended)

        if extended:
            serializer_models = None
            serializer = ProjectModelVersionExtendedSerializer(data, many=True)

            if include_live_models:
                ml_models = project.get_ml_backends()
                serializer_models = MLBackendSerializer(ml_models, many=True)

            # serializer.is_valid(raise_exception=True)
            return Response({'static': serializer.data, 'live': serializer_models and serializer_models.data})
        else:
            return Response(data=data)

    def delete(self, request, *args, **kwargs):
        project = self.get_object()
        model_version = request.data.get('model_version', None)

        if not model_version:
            raise RestValidationError('model_version param is required')

        count = project.delete_predictions(model_version=model_version)

        return Response(data=count)


class DatasetVersionViewSet(viewsets.ModelViewSet):
    queryset = DatasetVersion.objects.all()
    serializer_class = DatasetVersionSerializer
    filter_backends = [filters.OrderingFilter, DjangoFilterBackend]
    filterset_fields = ['project', 'name', 'version', 'created_by']
    ordering_fields = ['created_at', 'name', 'version']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = super().get_queryset()
        project_pk = self.kwargs.get('project_pk')
        if project_pk:
            queryset = queryset.filter(project_id=project_pk)
        return queryset

    def perform_create(self, serializer):
        from .tasks import process_version_creation
        from core.redis import start_job_async_or_sync
        project_pk = self.kwargs.get('project_pk')
        project = generics.get_object_or_404(Project.objects.all(), pk=project_pk)
        
        # 获取split_config数据
        split_config = self.request.data.get('split_config', {})
        logger.info(f"前端发送的 split_config: {split_config}")
        
        version = serializer.save(
            created_by=self.request.user,
            project=project,
            status=DatasetVersion.Status.CREATED,
            split_config=split_config  # 显式保存split_config
        )
        
        # 执行任务分割
        if split_config:
            logger.info(f"开始为版本 {version.id} 执行任务分割")
            split_tasks_for_version(version, split_config)
        else:
            logger.warning(f"版本 {version.id} 没有 split_config，跳过任务分割")
            
        # 启动Pachyderm处理任务
        start_job_async_or_sync(process_version_creation, version.id)

    @swagger_auto_schema(
        tags=['Dataset Versions'],
        operation_summary='Auto split tasks for a dataset version',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'train_percent': openapi.Schema(type=openapi.TYPE_NUMBER, format=openapi.FORMAT_FLOAT, default=0.7),
                'validation_percent': openapi.Schema(type=openapi.TYPE_NUMBER, format=openapi.FORMAT_FLOAT, default=0.2),
                'test_percent': openapi.Schema(type=openapi.TYPE_NUMBER, format=openapi.FORMAT_FLOAT, default=0.1),
            },
            required=['train_percent', 'validation_percent', 'test_percent']
        ),
        responses={200: 'Tasks split successfully'}
    )
    @action(detail=True, methods=['post'], url_path='auto-split')
    def auto_split(self, request, pk=None, project_pk=None):
        version = self.get_object()
        train_percent = request.data.get('train_percent')
        validation_percent = request.data.get('validation_percent')
        test_percent = request.data.get('test_percent')

        if not all([train_percent, validation_percent, test_percent]):
            raise RestValidationError('Missing one or more split percentages.')

        if not (0 <= train_percent <= 1 and 0 <= validation_percent <= 1 and 0 <= test_percent <= 1):
            raise RestValidationError('Percentages must be between 0 and 1.')
        
        if abs(train_percent + validation_percent + test_percent - 1.0) > 1e-6:
            raise RestValidationError('Percentages must sum to 1.0.')

        # 转换为百分比格式，与前端格式保持一致
        split_config = {
            'enabled': True,
            'train': int(train_percent * 100),
            'valid': int(validation_percent * 100),
            'test': int(test_percent * 100),
        }
        
        logger.info(f"Auto split配置: {split_config}")
        
        # Clear existing version tasks before re-splitting
        VersionTask.objects.filter(version=version).delete()
        split_tasks_for_version(version, split_config)

        version.split_config = split_config
        version.save()
        
        return Response({'status': 'Tasks split successfully'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='create-export')
    def create_export(self, request, pk=None, project_pk=None):
        """创建数据集导出任务，支持持久化导出结果"""
        from .tasks import process_dataset_export
        from core.redis import start_job_async_or_sync
        from . import pachyderm_utils as pachu
        
        version = self.get_object()
        export_format = request.data.get('format', 'YOLO')
        
        logger.info(f"创建导出请求 - 项目: {project_pk}, 版本: {pk}, 格式: {export_format}")
        logger.info(f"版本状态: {version.status}")
        
        # 检查版本配置是否已创建
        if version.status != DatasetVersion.Status.CREATED:
            logger.warning(f"版本 {pk} 状态不是CREATED，当前状态: {version.status}")
            return Response({
                'error': '版本配置尚未创建完成',
                'status': version.status,
                'message': '请等待版本配置创建完成后再进行导出'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not version.pachyderm_input_commit:
            logger.warning(f"版本 {pk} 没有有效的配置commit")
            return Response({
                'error': '版本配置不完整',
                'message': '版本配置文件不存在，无法进行导出'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # 生成导出唯一标识
        export_key = pachu.generate_export_key(project_pk, pk, export_format)
        
        # 检查持久化仓库中是否已存在相同的导出结果
        try:
            client = pachu.get_pachyderm_client()
            persistent_exists, persistent_commit_id = pachu.check_export_exists_in_persistent_repo(client, export_key)
            
            if persistent_exists:
                logger.info(f"在持久化仓库中找到现有导出: {export_key}")
                # 生成指向持久化仓库的下载链接
                download_url = f"/api/projects/{project_pk}/dataset-versions/{pk}/download-persistent/{export_key}/"
                
                # 检查或创建数据库记录
                existing_export = DatasetExport.objects.filter(
                    dataset_version=version,
                    format=export_format,
                    status=DatasetExport.ExportStatus.COMPLETED
                ).first()
                
                if not existing_export:
                    # 创建数据库记录来追踪这个持久化的导出
                    existing_export = DatasetExport.objects.create(
                        dataset_version=version,
                        format=export_format,
                        include_original=True,
                        include_augmented=True,
                        created_by=request.user,
                        status=DatasetExport.ExportStatus.COMPLETED,
                        progress=100.0,
                        download_url=download_url,
                        started_at=timezone.now(),
                        completed_at=timezone.now(),
                        pachyderm_pipeline_name="export-results",  # 标记为持久化仓库
                        pachyderm_output_commit=persistent_commit_id
                    )
                
                return Response({
                    'id': existing_export.id,
                    'status': existing_export.status,
                    'download_url': download_url,
                    'file_size': existing_export.file_size,
                    'completed_at': existing_export.completed_at,
                    'progress': 100.0,
                    'is_existing': True,
                    'message': '检测到持久化的导出结果，直接下载即可'
                })
                
        except Exception as e:
            logger.warning(f"检查持久化导出失败: {e}，继续进行新导出")
        
        # 检查是否已存在相同配置的进行中或已完成的导出（数据库记录）
        existing_export = DatasetExport.objects.filter(
            dataset_version=version,
            format=export_format,
            status__in=[DatasetExport.ExportStatus.PROCESSING, DatasetExport.ExportStatus.COMPLETED]
        ).first()
        
        if existing_export:
            if existing_export.status == DatasetExport.ExportStatus.COMPLETED:
                return Response({
                    'id': existing_export.id,
                    'status': existing_export.status,
                    'download_url': existing_export.download_url,
                    'file_size': existing_export.file_size,
                    'completed_at': existing_export.completed_at,
                    'progress': 100.0,
                    'is_existing': True,
                    'message': '已存在相同配置的导出，直接下载即可'
                })
            else:
                return Response({
                    'id': existing_export.id,
                    'status': existing_export.status,
                    'progress': existing_export.progress,
                    'is_existing': True,
                    'message': '相同配置的导出正在处理中，请稍后...'
                })
        
        # 创建新的导出记录
        export_record = DatasetExport.objects.create(
            dataset_version=version,
            format=export_format,
            include_original=True,
            include_augmented=True,
            created_by=request.user,
            status=DatasetExport.ExportStatus.PROCESSING,
            progress=0.0,
            started_at=timezone.now()
        )
        
        # 启动导出处理任务，传递export_key用于持久化
        start_job_async_or_sync(process_dataset_export, export_record.id, version.id, export_key)
        
        logger.info(f"导出任务已启动: export_id={export_record.id}, version_id={version.id}, export_key={export_key}")
        
        return Response({
            'id': export_record.id,
            'status': export_record.status,
            'progress': export_record.progress,
            'message': '导出任务已启动，正在动态创建管道并处理数据',
            'estimated_time': '预计3-5分钟完成'
        })

    @action(detail=True, methods=['get'], url_path='export-status/(?P<export_id>[^/.]+)')
    def export_status(self, request, pk=None, project_pk=None, export_id=None):
        """检查导出任务状态"""
        
        try:
            export_record = DatasetExport.objects.get(id=export_id, dataset_version_id=pk)
        except DatasetExport.DoesNotExist:
            return Response({'error': '导出记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        
        # 返回导出状态
        response_data = {
            'id': export_record.id,
            'status': export_record.status,
            'progress': export_record.progress,
            'format': export_record.format,
            'is_existing': True,  # 通过状态查询接口访问的都是现有导出
        }
        
        if export_record.status == DatasetExport.ExportStatus.COMPLETED:
            response_data.update({
                'download_url': export_record.download_url,
                'file_size': export_record.file_size,
                'completed_at': export_record.completed_at,
                'message': '现有导出已完成，可直接下载'
            })
        elif export_record.status == DatasetExport.ExportStatus.PROCESSING:
            response_data['message'] = '现有导出正在处理中，请等待完成'
        elif export_record.status == DatasetExport.ExportStatus.FAILED:
            response_data['error_message'] = export_record.error_message
            response_data['message'] = '现有导出处理失败'
            
        return Response(response_data)
    
    def _generate_download_url(self, pipeline_name, output_commit):
        """生成下载链接"""
        # 生成指向我们自己下载端点的URL
        return f"/api/projects/{self.kwargs['project_pk']}/dataset-versions/{self.kwargs['pk']}/download-export/{pipeline_name}/{output_commit.id}/"

    @action(detail=True, methods=['get'], url_path='debug-split')
    def debug_split(self, request, pk=None, project_pk=None):
        """调试分割数据的端点"""
        version = self.get_object()
        
        # 获取所有 VersionTask 记录
        version_tasks = VersionTask.objects.filter(version=version)
        tasks_data = []
        for vt in version_tasks:
            tasks_data.append({
                'task_id': vt.task_id,
                'subset': vt.subset
            })
        
        # 统计信息
        total_tasks = Task.objects.filter(project=version.project).count()
        total_version_tasks = version_tasks.count()
        train_count = version_tasks.filter(subset='train').count()
        valid_count = version_tasks.filter(subset='valid').count()
        test_count = version_tasks.filter(subset='test').count()
        
        debug_info = {
            'version_id': version.id,
            'version_name': version.name,
            'split_config': version.split_config,
            'project_total_tasks': total_tasks,
            'version_total_tasks': total_version_tasks,
            'split_counts': {
                'train': train_count,
                'valid': valid_count,
                'test': test_count
            },
            'version_tasks': tasks_data[:10],  # 只显示前10个
            'version_tasks_count': len(tasks_data)
        }
        
        logger.info(f"调试信息 - 版本 {version.id}: {debug_info}")
        return Response(debug_info)

    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, pk=None, project_pk=None):
        """传统的直接导出方法（向后兼容）"""
        version = self.get_object()
        project = version.project
        export_type = request.GET.get('exportType', 'JSON')
        download_resources = request.GET.get('download_resources', 'false').lower() == 'true'

        task_ids = VersionTask.objects.filter(version=version).values_list('task_id', flat=True)
        tasks = Task.objects.filter(id__in=task_ids)
        
        # Use a simplified serializer for export
        exported_tasks = ExportDataSerializer(tasks, many=True, context={'interpolate_key_frames': False}).data

        export_file, content_type, filename = DataExport.generate_export_file(
            project, exported_tasks, export_type, download_resources, request.GET, hostname=request.build_absolute_uri('/')
        )

        response = FileResponse(export_file, as_attachment=True, content_type=content_type, filename=filename)
        response['filename'] = filename
        return response

    @action(detail=False, methods=['get'], url_path='available-transforms')
    def available_transforms(self, request, *args, **kwargs):
        """返回可用的数据预处理和增强配置选项"""
        from data_transforms.preprocessing import AVAILABLE_PREPROCESSING
        from data_transforms.augmentation import AVAILABLE_AUGMENTATION

        # We don't want to send the function object in the response
        def clean_config(config):
            return {
                name: {k: v for k, v in details.items() if k != 'function'}
                for name, details in config.items()
            }

        return Response({
            'preprocessing': clean_config(AVAILABLE_PREPROCESSING),
            'augmentation': clean_config(AVAILABLE_AUGMENTATION)
        })
    
    @action(detail=True, methods=['get'], url_path='processed-files')
    def get_processed_files(self, request, pk=None, project_pk=None):
        """获取版本处理后的文件列表"""
        version = self.get_object()
        
        if not version.pachyderm_output_commit:
            return Response({'error': '版本尚未处理完成'}, status=400)
            
        try:
            from . import pachyderm_utils as pachu
            client = pachu.get_pachyderm_client()
            
            # 创建 commit 对象
            from pachyderm_sdk.api import pfs
            commit = pfs.Commit(
                repo=pfs.Repo(name=f"ls-output-{version.project.id}"),
                id=version.pachyderm_output_commit
            )
            
            processed_files = pachu.get_processed_files_from_version(client, commit)
            
            return Response({
                'files': processed_files,
                'total_count': len(processed_files),
                'version_id': version.id,
                'commit_id': version.pachyderm_output_commit
            })
            
        except Exception as e:
            logger.error(f"获取处理文件失败 version {version.id}: {e}")
            return Response({'error': f'获取处理文件失败: {str(e)}'}, status=500)
    
    @action(detail=True, methods=['post'], url_path='retry-processing')
    def retry_processing(self, request, pk=None, project_pk=None):
        """重试失败的版本处理"""
        version = self.get_object()
        
        if version.status not in [DatasetVersion.Status.FAILED, DatasetVersion.Status.CREATED]:
            return Response({'error': '只能重试失败或未开始的版本'}, status=400)
            
        # 重置状态并重新开始处理
        version.status = DatasetVersion.Status.CREATED
        version.error_message = None
        version.pachyderm_input_commit = None
        version.pachyderm_output_commit = None
        version.processed_at = None
        version.save()
        
        # 重新启动处理任务
        from core.redis import start_job_async_or_sync
        from .tasks import process_version_creation
        start_job_async_or_sync(process_version_creation, version.id)
        
        return Response({'message': '版本处理已重新开始'})

    @action(detail=True, methods=['get'], url_path='download-export/(?P<pipeline_name>[^/]+)/(?P<commit_id>[^/]+)')
    def download_export(self, request, pk=None, project_pk=None, pipeline_name=None, commit_id=None):
        """下载导出的数据集压缩包"""
        import tempfile
        import zipfile
        import os
        from django.http import HttpResponse
        from .pachyderm_utils import get_pachyderm_client
        from pachyderm_sdk.api import pfs
        
        def get_all_files_recursive(client, commit, path="/"):
            """递归获取目录下的所有文件"""
            all_files = []
            try:
                file_obj = pfs.File(commit=commit, path=path)
                files = list(client.pfs.list_file(file=file_obj))
                
                for file_info in files:
                    if file_info.file_type == pfs.FileType.FILE:
                        # 这是一个文件
                        all_files.append(file_info)
                    elif file_info.file_type == pfs.FileType.DIR:
                        # 这是一个目录，递归获取其中的文件
                        subdir_files = get_all_files_recursive(client, commit, file_info.file.path)
                        all_files.extend(subdir_files)
                        
            except Exception as e:
                logger.warning(f"获取路径 {path} 下的文件时出错: {e}")
                
            return all_files
        
        try:
            version = self.get_object()
            client = get_pachyderm_client()
            
            logger.info(f"开始下载导出 - 管道: {pipeline_name}, commit: {commit_id}")
            
            # 下载Pachyderm仓库中的所有文件
            commit = pfs.Commit(
                repo=pfs.Repo(name=pipeline_name),
                id=commit_id
            )
            
            # 递归获取所有文件（包括子目录）
            all_files = get_all_files_recursive(client, commit, "/")
            
            logger.info(f"递归找到 {len(all_files)} 个文件")
            
            # 创建内存中的压缩包
            import io
            zip_buffer = io.BytesIO()
            zip_filename = f"{pipeline_name}_{commit_id[:8]}.zip"
            
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_info in all_files:
                    file_path = file_info.file.path
                    logger.info(f"处理文件: {file_path}")
                    
                    # 下载文件内容
                    file_obj = pfs.File(commit=commit, path=file_path)
                    file_content = client.pfs.get_file(file=file_obj)
                    
                    # 处理不同的返回类型
                    content_bytes = b''
                    if hasattr(file_content, 'read'):
                        # 如果是文件类对象
                        content_bytes = file_content.read()
                        logger.info(f"文件对象读取: {len(content_bytes)} 字节")
                    else:
                        # 如果是生成器，遍历所有数据块
                        chunk_count = 0
                        for chunk in file_content:
                            chunk_count += 1
                            if hasattr(chunk, 'value'):
                                # 如果是 BytesValue 对象，提取 value 属性
                                content_bytes += chunk.value
                            elif isinstance(chunk, bytes):
                                # 如果是普通字节
                                content_bytes += chunk
                            else:
                                # 尝试转换为字节
                                content_bytes += bytes(chunk)
                        logger.info(f"生成器读取: {chunk_count} 个块, 总计 {len(content_bytes)} 字节")
                    
                    # 添加到压缩包，去除前导斜杠
                    archive_path = file_path.lstrip('/')
                    zipf.writestr(archive_path, content_bytes)
                    logger.info(f"已添加到压缩包: {archive_path}")
            
            logger.info(f"压缩包创建完成: {zip_filename}")
            
            # 返回压缩包
            zip_buffer.seek(0)
            response = HttpResponse(
                zip_buffer.getvalue(),
                content_type='application/zip'
            )
            response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
            response['Content-Length'] = len(zip_buffer.getvalue())
            
            # 查找对应的导出记录，并触发管道清理
            try:
                export_record = DatasetExport.objects.filter(
                    dataset_version=version,
                    pachyderm_pipeline_name=pipeline_name,
                    pachyderm_output_commit=commit_id,
                    status=DatasetExport.ExportStatus.COMPLETED
                ).first()
                
                if export_record:
                    # 启动异步管道清理任务
                    from .tasks import cleanup_export_pipelines
                    from core.redis import start_job_async_or_sync
                    
                    # 启动管道清理任务（立即执行，不延迟）
                    start_job_async_or_sync(cleanup_export_pipelines, export_record.id)
                    logger.info(f"已启动管道清理任务: export_id={export_record.id}")
                    
            except Exception as cleanup_e:
                logger.warning(f"启动管道清理失败: {cleanup_e}")
                # 不影响下载，继续返回文件
            
            return response
                
        except Exception as e:
            logger.error(f"下载导出文件失败: {e}")
            return Response({
                'error': '下载失败',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='exports/(?P<export_id>[^/.]+)/download')
    def download_export_by_id(self, request, pk=None, project_pk=None, export_id=None):
        """通过export_id下载导出文件（重定向到实际下载链接）"""
        try:
            version = self.get_object()
            export_record = DatasetExport.objects.get(id=export_id, dataset_version=version)
            
            if export_record.status != DatasetExport.ExportStatus.COMPLETED:
                return Response({
                    'error': '导出尚未完成',
                    'status': export_record.status
                }, status=status.HTTP_400_BAD_REQUEST)
            
            if not export_record.download_url:
                return Response({
                    'error': '下载链接不可用'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # 重定向到实际的下载链接
            from django.http import HttpResponseRedirect
            return HttpResponseRedirect(export_record.download_url)
            
        except DatasetExport.DoesNotExist:
            return Response({'error': '导出记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"通过export_id下载失败: {e}")
            return Response({
                'error': '下载失败',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='download-persistent/(?P<export_key>[^/]+)')
    def download_persistent_export(self, request, pk=None, project_pk=None, export_key=None):
        """从持久化仓库下载导出文件"""
        import io
        import zipfile
        from django.http import HttpResponse
        from .pachyderm_utils import get_pachyderm_client
        from pachyderm_sdk.api import pfs
        
        def get_all_files_recursive(client, commit, path="/"):
            """递归获取目录下的所有文件"""
            all_files = []
            try:
                file_obj = pfs.File(commit=commit, path=path)
                files = list(client.pfs.list_file(file=file_obj))
                
                for file_info in files:
                    if file_info.file_type == pfs.FileType.FILE:
                        all_files.append(file_info)
                    elif file_info.file_type == pfs.FileType.DIR:
                        subdir_files = get_all_files_recursive(client, commit, file_info.file.path)
                        all_files.extend(subdir_files)
                        
            except Exception as e:
                logger.warning(f"获取路径 {path} 下的文件时出错: {e}")
                
            return all_files
        
        try:
            version = self.get_object()
            client = get_pachyderm_client()
            
            logger.info(f"开始从持久化仓库下载导出: {export_key}")
            
            # 连接持久化仓库
            export_repo_name = "export-results"
            
            # 使用辅助函数获取master分支的head commit
            from .pachyderm_utils import get_master_commit_from_repo
            master_commit = get_master_commit_from_repo(client, export_repo_name)
            
            if not master_commit:
                raise Exception("持久化仓库没有master分支")
            
            # 获取指定导出的所有文件
            export_path = f"/{export_key}"
            all_files = get_all_files_recursive(client, master_commit, export_path)
            
            if not all_files:
                raise Exception(f"持久化仓库中未找到导出: {export_key}")
            
            logger.info(f"在持久化仓库中找到 {len(all_files)} 个文件")
            
            # 创建内存中的压缩包
            zip_buffer = io.BytesIO()
            zip_filename = f"{export_key}.zip"
            
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_info in all_files:
                    file_path = file_info.file.path
                    logger.debug(f"处理文件: {file_path}")
                    
                    # 下载文件内容
                    file_obj = pfs.File(commit=master_commit, path=file_path)
                    file_content = client.pfs.get_file(file=file_obj)
                    
                    # 处理不同的返回类型
                    content_bytes = b''
                    if hasattr(file_content, 'read'):
                        content_bytes = file_content.read()
                    else:
                        for chunk in file_content:
                            if hasattr(chunk, 'value'):
                                content_bytes += chunk.value
                            elif isinstance(chunk, bytes):
                                content_bytes += chunk
                            else:
                                content_bytes += bytes(chunk)
                    
                    # 计算在压缩包中的相对路径（去除export_key前缀）
                    if file_path.startswith(f"/{export_key}/"):
                        archive_path = file_path[len(f"/{export_key}/"):]
                    else:
                        archive_path = file_path.lstrip('/')
                    
                    # 跳过元数据文件
                    if archive_path == "_metadata.json":
                        continue
                        
                    zipf.writestr(archive_path, content_bytes)
                    logger.debug(f"已添加到压缩包: {archive_path}")
            
            logger.info(f"持久化导出压缩包创建完成: {zip_filename}")
            
            # 返回压缩包
            zip_buffer.seek(0)
            response = HttpResponse(
                zip_buffer.getvalue(),
                content_type='application/zip'
            )
            response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
            response['Content-Length'] = len(zip_buffer.getvalue())
            
            return response
                
        except Exception as e:
            logger.error(f"从持久化仓库下载失败: {e}")
            return Response({
                'error': '从持久化仓库下载失败',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def split_tasks_for_version(version, split_config):
    project = version.project
    tasks = Task.objects.filter(project=project)
    task_ids = list(tasks.values_list('id', flat=True))
    
    logger.info(f"分割版本 {version.id} 的任务，总任务数: {len(task_ids)}")
    logger.info(f"分割配置: {split_config}")
    
    # 检查配置是否启用
    if not split_config.get('enabled', True):
        logger.info("分割功能未启用，跳过任务分割")
        return
    
    # 处理百分比格式（前端发送的是 0-100 的整数）
    train_percent = split_config.get('train', 70)
    valid_percent = split_config.get('valid', 20) 
    test_percent = split_config.get('test', 10)
    
    # 转换为 0-1 之间的比例
    train_ratio = train_percent / 100.0
    valid_ratio = valid_percent / 100.0
    test_ratio = test_percent / 100.0
    
    logger.info(f"百分比配置: train={train_percent}%, valid={valid_percent}%, test={test_percent}%")
    logger.info(f"转换后的比例: train={train_ratio}, valid={valid_ratio}, test={test_ratio}")
    
    # 使用更智能的分配策略，避免因取整导致的空集合
    total_tasks = len(task_ids)
    
    # 先计算理想的任务数量
    ideal_train = total_tasks * train_ratio
    ideal_valid = total_tasks * valid_ratio
    ideal_test = total_tasks * test_ratio
    
    # 使用round而不是int来更均匀地分配
    train_size = round(ideal_train)
    valid_size = round(ideal_valid)
    test_size = round(ideal_test)
    
    # 确保总和等于任务总数
    total_assigned = train_size + valid_size + test_size
    if total_assigned != total_tasks:
        # 调整最大的集合来匹配总数
        diff = total_tasks - total_assigned
        if train_size >= valid_size and train_size >= test_size:
            train_size += diff
        elif valid_size >= test_size:
            valid_size += diff
        else:
            test_size += diff
    
    # 确保每个集合至少有一个任务（如果总任务数足够）
    if total_tasks >= 3:
        if train_size == 0:
            train_size = 1
        if valid_size == 0:
            valid_size = 1
        if test_size == 0:
            test_size = 1
            
        # 重新平衡以确保总和正确
        total_assigned = train_size + valid_size + test_size
        if total_assigned > total_tasks:
            # 从最大的集合中减去多余的任务
            excess = total_assigned - total_tasks
            if train_size > max(valid_size, test_size):
                train_size -= excess
            elif valid_size > test_size:
                valid_size -= excess
            else:
                test_size -= excess
    
    logger.info(f"智能分割: train_size={train_size}, valid_size={valid_size}, test_size={test_size}, 总计={train_size + valid_size + test_size}")
    
    # 随机打乱任务顺序，确保分割的随机性
    import random
    random.shuffle(task_ids)
    
    train_tasks = task_ids[:train_size]
    valid_tasks = task_ids[train_size:train_size + valid_size]
    test_tasks = task_ids[train_size + valid_size:train_size + valid_size + test_size]
    
    version_tasks = []
    for task_id in train_tasks:
        version_tasks.append(VersionTask(version=version, task_id=task_id, subset='train'))
    for task_id in valid_tasks:
        version_tasks.append(VersionTask(version=version, task_id=task_id, subset='valid'))
    for task_id in test_tasks:
        version_tasks.append(VersionTask(version=version, task_id=task_id, subset='test'))
        
    VersionTask.objects.bulk_create(version_tasks)
    
    logger.info(f"成功创建 {len(version_tasks)} 个VersionTask记录")
    
    # 验证分割结果
    train_count = VersionTask.objects.filter(version=version, subset='train').count()
    valid_count = VersionTask.objects.filter(version=version, subset='valid').count()
    test_count = VersionTask.objects.filter(version=version, subset='test').count()
    logger.info(f"分割结果验证: train={train_count}, valid={valid_count}, test={test_count}")


class ProjectSetActiveVersionAPI(generics.GenericAPIView):
    queryset = Project.objects.all()
    permission_required = all_permissions.projects_change
    serializer_class = ProjectSerializer

    def post(self, request, *args, **kwargs):
        project = self.get_object()
        version_id = request.data.get('version_id')
        if not version_id:
            project.active_version = None
            project.save()
            return Response({'detail': 'Active version cleared.'}, status=status.HTTP_200_OK)

        try:
            version = DatasetVersion.objects.get(id=version_id, project=project)
            project.active_version = version
            project.save()
            return Response(ProjectSerializer(project).data)
        except DatasetVersion.DoesNotExist:
            raise NotFound('Version not found in this project.')


class VersionTaskViewSet(viewsets.ModelViewSet):
    queryset = VersionTask.objects.all()
    serializer_class = VersionTaskSerializer
    filter_backends = [filters.OrderingFilter, DjangoFilterBackend]
    filterset_fields = ['version', 'task', 'subset']
    ordering_fields = ['version', 'task', 'subset']
    ordering = ['version']
