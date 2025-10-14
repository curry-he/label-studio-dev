"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license.
"""
import logging
import numpy as np
from PIL import Image
import albumentations as A
import cv2

logger = logging.getLogger(__name__)

def apply_preprocessing(image, annotations, config):
    """
    使用albumentation应用预处理步骤到图像和标注。
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

    # 将Label Studio标注转换为albumentation格式
    bboxes = []
    class_labels = []
    keypoints = []
    keypoint_labels = []
    
    for ann in annotations:
        for r in ann.get('result', []):
            value = r.get('value', {})
            annotation_type = r.get('type')
            
            if annotation_type == 'rectanglelabels' and 'x' in value and 'y' in value and 'width' in value and 'height' in value:
                # 处理矩形标注
                x, y, w, h = value['x'], value['y'], value['width'], value['height']
                # 从百分比转换为绝对像素值
                abs_x = x * img_width / 100
                abs_y = y * img_height / 100
                abs_w = w * img_width / 100
                abs_h = h * img_height / 100
                
                # albumentation期望 [x_min, y_min, x_max, y_max]
                bboxes.append([abs_x, abs_y, abs_x + abs_w, abs_y + abs_h])
                class_labels.append(value.get('rectanglelabels', ['unknown'])[0])
                
            elif annotation_type == 'keypointlabels' and 'x' in value and 'y' in value:
                # 处理关键点标注
                x, y = value['x'], value['y']
                # 从百分比转换为绝对像素值
                abs_x = x * img_width / 100
                abs_y = y * img_height / 100
                
                # albumentation期望 [x, y, visibility] visibility=2表示可见
                keypoints.append([abs_x, abs_y, 2])
                keypoint_labels.append(value.get('keypointlabels', ['unknown'])[0])

    try:
        # 创建变换管道
        if bboxes and keypoints:
            # 同时有边界框和关键点
            transform = A.Compose(
                transform_pipeline,
                bbox_params=A.BboxParams(format='pascal_voc', label_fields=['class_labels']),
                keypoint_params=A.KeypointParams(format='xy', label_fields=['keypoint_labels'])
            )
            # 应用包含边界框和关键点的变换
            transformed = transform(
                image=image_np, 
                bboxes=bboxes, 
                class_labels=class_labels,
                keypoints=keypoints,
                keypoint_labels=keypoint_labels
            )
            transformed_bboxes = transformed['bboxes']
            transformed_labels = transformed['class_labels']
            transformed_keypoints = transformed['keypoints']
            transformed_keypoint_labels = transformed['keypoint_labels']
        elif bboxes:
            # 只有边界框
            transform = A.Compose(
                transform_pipeline,
                bbox_params=A.BboxParams(format='pascal_voc', label_fields=['class_labels'])
            )
            # 应用包含边界框的变换
            transformed = transform(image=image_np, bboxes=bboxes, class_labels=class_labels)
            transformed_bboxes = transformed['bboxes']
            transformed_labels = transformed['class_labels']
            transformed_keypoints = []
            transformed_keypoint_labels = []
        elif keypoints:
            # 只有关键点
            transform = A.Compose(
                transform_pipeline,
                keypoint_params=A.KeypointParams(format='xy', label_fields=['keypoint_labels'])
            )
            # 应用包含关键点的变换
            transformed = transform(image=image_np, keypoints=keypoints, keypoint_labels=keypoint_labels)
            transformed_bboxes = []
            transformed_labels = []
            transformed_keypoints = transformed['keypoints']
            transformed_keypoint_labels = transformed['keypoint_labels']
        else:
            # 没有边界框或关键点，只对图像应用变换
            transform = A.Compose(transform_pipeline)
            transformed = transform(image=image_np)
            transformed_bboxes = []
            transformed_labels = []
            transformed_keypoints = []
            transformed_keypoint_labels = []
        
        transformed_image = Image.fromarray(transformed['image'])
        
        # 将变换后的边界框和关键点转换回Label Studio格式
        new_annotations = []
        if (bboxes and transformed_bboxes) or (keypoints and transformed_keypoints):
            new_img_height, new_img_width = transformed['image'].shape[:2]
            
            bbox_idx = 0
            keypoint_idx = 0
            
            for ann in annotations:
                new_result = []
                for r in ann.get('result', []):
                    value = r.get('value', {})
                    annotation_type = r.get('type')
                    
                    if annotation_type == 'rectanglelabels' and 'x' in value and 'y' in value and 'width' in value and 'height' in value:
                        # 处理矩形标注变换
                        if bbox_idx < len(transformed_bboxes):
                            x_min, y_min, x_max, y_max = transformed_bboxes[bbox_idx]
                            
                            # 转换回百分比坐标
                            new_x = (x_min / new_img_width) * 100
                            new_y = (y_min / new_img_height) * 100
                            new_width = ((x_max - x_min) / new_img_width) * 100
                            new_height = ((y_max - y_min) / new_img_height) * 100
                            
                            r['value']['x'] = new_x
                            r['value']['y'] = new_y
                            r['value']['width'] = new_width
                            r['value']['height'] = new_height
                            
                            bbox_idx += 1
                    
                    elif annotation_type == 'keypointlabels' and 'x' in value and 'y' in value:
                        # 处理关键点标注变换
                        if keypoint_idx < len(transformed_keypoints):
                            new_x_abs, new_y_abs, visibility = transformed_keypoints[keypoint_idx]
                            
                            # 转换回百分比坐标
                            new_x = (new_x_abs / new_img_width) * 100
                            new_y = (new_y_abs / new_img_height) * 100
                            
                            r['value']['x'] = new_x
                            r['value']['y'] = new_y
                            # 保持原有的width属性（如果存在）
                            
                            keypoint_idx += 1
                    
                    new_result.append(r)
                new_annotations.append({'id': ann.get('id'), 'result': new_result})
        else:
            new_annotations = annotations
        
        return transformed_image, new_annotations, applied_params
        
    except Exception as e:
        logger.error(f"预处理失败: {e}")
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