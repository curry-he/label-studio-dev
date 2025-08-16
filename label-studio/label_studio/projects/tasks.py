"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license.
"""
import logging
from PIL import Image
import os
from django.core.files.base import ContentFile
from django.conf import settings
from io import BytesIO

import json
from .models import DatasetVersion, Task, VersionTask
from .pachyderm_utils import get_pach_client, create_repo, commit_file, create_pipeline

logger = logging.getLogger(__name__)

def process_version_creation(version_id):
    """
    Asynchronous task to trigger a Pachyderm pipeline for a new dataset version.
    This task will:
    1.  Create Pachyderm repos if they don't exist.
    2.  Commit the version's configuration and raw data to an input repo.
    3.  Create/update a Pachyderm pipeline to process the data.
    4.  Update the version status to 'processing'.
    """
    try:
        version = DatasetVersion.objects.get(id=version_id)
        version.status = DatasetVersion.Status.PROCESSING
        version.save()

        project = version.project
        client = get_pach_client()

        # Define repo names
        input_repo_name = f"project-{project.id}-input"
        output_repo_name = f"project-{project.id}-output"

        # Create repos if they don't exist
        create_repo(client, input_repo_name)
        create_repo(client, output_repo_name)

        # Commit version config to the input repo
        config_data = {
            "preprocessing": version.preprocessing_config,
            "augmentation": version.augmentation_config,
            "split": version.split_config,
        }
        commit_file(client, input_repo_name, f"/{version.id}/config.json", json.dumps(config_data).encode('utf-8'))

        # Commit all project tasks to the input repo
        tasks = Task.objects.filter(project=project)
        for task in tasks:
            image_path = task.data.get('image')
            if image_path:
                if image_path.startswith('/data/upload'):
                    full_image_path = os.path.join(settings.MEDIA_ROOT, '..') + image_path
                else:
                    full_image_path = os.path.join(settings.MEDIA_ROOT, image_path)
                
                if os.path.exists(full_image_path):
                    with open(full_image_path, 'rb') as f:
                        commit_file(client, input_repo_name, f"/{version.id}/data/{os.path.basename(image_path)}", f.read())

        # Create/update the pipeline
        pipeline_name = f"project-{project.id}-pipeline"
        create_pipeline(
            client=client,
            pipeline_name=pipeline_name,
            image="your-data-processing-docker-image:latest",  # Replace with your actual Docker image
            cmd=["python", "/app/process_data.py", f"/pfs/{input_repo_name}/{version.id}/", f"/pfs/out/"],
            input_repo=input_repo_name,
            output_repo=output_repo_name,
        )

    except DatasetVersion.DoesNotExist:
        logger.error(f"DatasetVersion with id {version_id} not found.")
    except Exception as e:
        logger.error(f"Failed to trigger Pachyderm pipeline for version {version_id}: {e}", exc_info=True)
        try:
            version = DatasetVersion.objects.get(id=version_id)
            version.status = DatasetVersion.Status.FAILED
            version.save()
        except DatasetVersion.DoesNotExist:
            pass


def split_tasks_for_version(version, split_config):
    project = version.project
    tasks = Task.objects.filter(project=project).order_by('id') # Ensure consistent order
    task_ids = list(tasks.values_list('id', flat=True))

    train_ratio = split_config.get('train', 0.7)
    valid_ratio = split_config.get('validation_percent', 0.2)
    test_ratio = 1.0 - train_ratio - valid_ratio

    if test_ratio < 0:
        test_ratio = 0
        valid_ratio = 1.0 - train_ratio
        logger.warning(f"Train and validation split for version {version.id} exceeds 100%. Adjusting validation to {valid_ratio}.")


    train_size = int(len(task_ids) * train_ratio)
    valid_size = int(len(task_ids) * valid_ratio)

    train_tasks = task_ids[:train_size]
    valid_tasks = task_ids[train_size : train_size + valid_size]
    test_tasks = task_ids[train_size + valid_size :]

    version_tasks = []
    for task_id in train_tasks:
        version_tasks.append(VersionTask(version=version, task_id=task_id, subset='train'))
    for task_id in valid_tasks:
        version_tasks.append(VersionTask(version=version, task_id=task_id, subset='valid'))
    for task_id in test_tasks:
        version_tasks.append(VersionTask(version=version, task_id=task_id, subset='test'))

    VersionTask.objects.bulk_create(version_tasks)