"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license.
"""
import logging
from PIL import Image
import os
from django.core.files.base import ContentFile
from django.conf import settings
from io import BytesIO

from .models import DatasetVersion, Task, VersionTask, ProcessedTask
from data_transforms.preprocessing import apply_preprocessing, transform_annotations as transform_annotations_preprocessing
from data_transforms.augmentation import apply_augmentation

logger = logging.getLogger(__name__)


def process_version_creation(version_id):
    """
    Asynchronous task to process a new dataset version.
    This task will:
    1. Split tasks into train/validation/test sets.
    2. Apply preprocessing to all tasks.
    3. Apply augmentation ONLY to the training set.
    4. Transform annotations to match the new image dimensions.
    5. Create ProcessedTask records with the new data.
    """
    try:
        version = DatasetVersion.objects.get(id=version_id)
        version.status = DatasetVersion.Status.PROCESSING
        version.save()

        project = version.project
        tasks = Task.objects.filter(project=project)

        # Step 1: Split tasks into train/validation/test sets first
        VersionTask.objects.filter(version=version).delete()  # Clear previous splits if any
        split_config = version.split_config
        if split_config:
            split_tasks_for_version(version, split_config)
        else:
            # Default split: all tasks go to the training set
            version_tasks = [VersionTask(version=version, task=task, subset='train') for task in tasks]
            VersionTask.objects.bulk_create(version_tasks)

        # Step 2: Apply preprocessing and augmentation based on the subset
        for version_task in version.tasks.all().select_related('task'):
            task = version_task.task
            try:
                image_path = task.data.get('image')
                if not image_path:
                    logger.warning(f"Task {task.id} has no image data.")
                    continue

                # Construct full image path (this might need adjustment based on storage)
                if image_path.startswith('/data/upload'):
                    full_image_path = os.path.join(settings.MEDIA_ROOT, '..') + image_path
                else:
                    full_image_path = os.path.join(settings.MEDIA_ROOT, image_path)

                if not os.path.exists(full_image_path):
                    logger.warning(f"Image file not found: {full_image_path} for task {task.id}")
                    continue

                with Image.open(full_image_path) as img:
                    original_width, original_height = img.size

                    # Apply preprocessing
                    processed_img, pp_params = apply_preprocessing(img, version.preprocessing_config)

                    # Conditionally apply augmentation for the training set
                    if version_task.subset == 'train':
                        processed_img, aug_params = apply_augmentation(processed_img, version.augmentation_config)
                    else:
                        aug_params = {}
                    
                    # Combine all transformation parameters
                    transform_params = {**pp_params, **aug_params}

                    # Process annotations to match transformations
                    processed_annotations_list = []
                    for annotation in task.annotations.filter(was_cancelled=False, result__isnull=False):
                        new_result = transform_annotations_preprocessing(
                            annotation.result,
                            transform_params,
                            original_width=original_width,
                            original_height=original_height,
                        )
                        processed_annotations_list.append({'id': annotation.id, 'result': new_result})

                    # Save the processed image to a buffer
                    buffer = BytesIO()
                    # Ensure the image is converted to a consistent format like RGB before saving
                    if processed_img.mode != 'RGB':
                        processed_img = processed_img.convert('RGB')
                    processed_img.save(buffer, format='PNG') # Save as PNG to handle transparency
                    buffer.seek(0)

                    # Create a Django ContentFile
                    file_name = os.path.splitext(os.path.basename(image_path))[0] + '.png'
                    content_file = ContentFile(buffer.read(), name=file_name)

                    # Create ProcessedTask
                    ProcessedTask.objects.create(
                        original_task=task,
                        version=version,
                        processed_data=content_file,
                        processed_annotations={'annotations': processed_annotations_list},
                    )
            except Exception as e:
                logger.error(f"Failed to process task {task.id} for version {version.id}: {e}", exc_info=True)

        version.status = DatasetVersion.Status.COMPLETED
        version.save()

    except DatasetVersion.DoesNotExist:
        logger.error(f"DatasetVersion with id {version_id} not found.")
    except Exception as e:
        logger.error(f"Failed to process version {version_id}: {e}", exc_info=True)
        try:
            version = DatasetVersion.objects.get(id=version_id)
            version.status = DatasetVersion.Status.FAILED
            version.save()
        except DatasetVersion.DoesNotExist:
            pass  # Version was not found in the first place


def split_tasks_for_version(version, split_config):
    project = version.project
    tasks = Task.objects.filter(project=project).order_by('id') # Ensure consistent order
    task_ids = list(tasks.values_list('id', flat=True))

    train_ratio = split_config.get('train', 0.7)
    valid_ratio = split_config.get('validation', 0.2) # Corrected key
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