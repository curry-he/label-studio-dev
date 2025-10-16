"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license.
"""
import logging
import numpy as np
from PIL import Image
import albumentations as A
import cv2
from label_sync_utils import AnnotationTracker

logger = logging.getLogger(__name__)

def apply_preprocessing(image, annotations, config):
    """
    使用albumentation应用预处理步骤到图像和标注（改进版 - 使用AnnotationTracker）
    :param image: PIL Image对象
    :param annotations: Label Studio标注结果列表
    :param config: 预处理配置列表
    :return: (处理后的PIL Image对象, 变换后的标注, 应用的变换参数字典)
    """
    if not config:
        return image, annotations, {}

    transform_pipeline = []
    applied_params = {}

    for step in config:
        func_name = step.get('type')
        params = step.get('params', {})
        if func_name in AVAILABLE_PREPROCESSING:
            func = AVAILABLE_PREPROCESSING[func_name]['function']
            preprocessing_transform = func(**params)
            transform_pipeline.append(preprocessing_transform)
            applied_params[func_name] = params

    if not transform_pipeline:
        return image, annotations, {}

    # 将PIL图像转换为numpy数组 (albumentation需要)
    image_np = np.array(image)
    img_height, img_width = image_np.shape[:2]

    # 使用AnnotationTracker提取标注
    tracker = AnnotationTracker(annotations)
    bboxes, class_labels, keypoints, keypoint_labels = tracker.extract_annotations_for_albumentations(
        img_width, img_height
    )

    try:
        # 根据标注类型创建变换
        if bboxes and keypoints:
            # 同时有边界框和关键点
            transform = A.Compose(
                transform_pipeline,
                bbox_params=A.BboxParams(
                    format='pascal_voc',
                    label_fields=['class_labels'],
                    min_area=0,  # 不过滤小bbox（预处理阶段）
                    min_visibility=0  # 不过滤低可见度bbox
                ),
                keypoint_params=A.KeypointParams(
                    format='xy',
                    label_fields=['keypoint_labels'],
                    remove_invisible=False
                )
            )
            transformed = transform(
                image=image_np,
                bboxes=bboxes,
                class_labels=class_labels,
                keypoints=keypoints,
                keypoint_labels=keypoint_labels
            )
            transformed_bboxes = transformed['bboxes']
            transformed_keypoints = transformed['keypoints']

        elif bboxes:
            # 只有边界框
            transform = A.Compose(
                transform_pipeline,
                bbox_params=A.BboxParams(
                    format='pascal_voc',
                    label_fields=['class_labels'],
                    min_area=0,
                    min_visibility=0
                )
            )
            transformed = transform(image=image_np, bboxes=bboxes, class_labels=class_labels)
            transformed_bboxes = transformed['bboxes']
            transformed_keypoints = []

        elif keypoints:
            # 只有关键点
            transform = A.Compose(
                transform_pipeline,
                keypoint_params=A.KeypointParams(
                    format='xy',
                    label_fields=['keypoint_labels'],
                    remove_invisible=False
                )
            )
            transformed = transform(image=image_np, keypoints=keypoints, keypoint_labels=keypoint_labels)
            transformed_bboxes = []
            transformed_keypoints = transformed['keypoints']

        else:
            # 没有边界框或关键点，只对图像应用变换
            transform = A.Compose(transform_pipeline)
            transformed = transform(image=image_np)
            transformed_bboxes = []
            transformed_keypoints = []

        transformed_image = Image.fromarray(transformed['image'])
        new_img_height, new_img_width = transformed['image'].shape[:2]

        # 使用追踪器重建标注
        new_annotations = tracker.rebuild_annotations_from_transformed(
            transformed_bboxes,
            transformed_keypoints,
            new_img_width,
            new_img_height
        )

        return transformed_image, new_annotations, applied_params

    except Exception as e:
        logger.error(f"预处理失败: {e}", exc_info=True)
        return image, annotations, {}


def resize(width=640, height=640, mode='stretch_to'):
    """
    创建resize变换。
    :param width: 目标宽度
    :param height: 目标高度
    :param mode: 缩放模式
    :return: albumentation变换对象
    """
    logger.info(f"创建resize变换: {width}x{height}, 模式: {mode}")

    if mode == 'stretch_to':
        return A.Resize(height, width, interpolation=cv2.INTER_LINEAR)
    elif mode == 'fit_within':
        return A.LongestMaxSize(max_size=max(width, height))
    elif mode == 'fill_center_crop':
        return A.Resize(height, width, interpolation=cv2.INTER_LINEAR)
    else:
        return A.Resize(height, width, interpolation=cv2.INTER_LINEAR)


def grayscale():
    """
    创建灰度化变换。
    :return: albumentation变换对象
    """
    logger.info("创建灰度化变换")
    return A.ToGray(always_apply=True)


# 可用的预处理配置 (只保留resize和grayscale)
AVAILABLE_PREPROCESSING = {
    "resize": {
        "name": "Resize",
        "description": "调整图像到指定尺寸",
        "function": resize,
        "params": [
            {"name": "width", "type": "number", "default": 640},
            {"name": "height", "type": "number", "default": 640},
            {"name": "mode", "type": "string", "default": "stretch_to"}
        ]
    },
    "grayscale": {
        "name": "Grayscale",
        "description": "将图像转换为灰度",
        "function": grayscale,
        "params": []
    }
}

def get_preprocessing_params(name):
    """获取预处理操作的参数配置"""
    return AVAILABLE_PREPROCESSING.get(name, {}).get('params', [])
