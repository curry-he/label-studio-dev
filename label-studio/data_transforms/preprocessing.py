from PIL import Image, ImageOps
import cv2
import numpy as np

def apply_preprocessing(image, config):
    """Applies a series of preprocessing steps to an image."""
    if not config:
        return image, {}

    params = {}
    for step in config:
        func = PREPROCESSING_FUNCTIONS.get(step['type'])
        if func:
            image, step_params = func(image, **step.get('params', {}))
            params.update(step_params)
    return image, params

def resize_image(image, width, height, mode='stretch_to'):
    """Resizes an image using various modes."""
    if mode == 'stretch_to':
        return image.resize((width, height), Image.LANCZOS), {'output_width': width, 'output_height': height}
    # Add other resize modes here (fit_within, fill_center_crop, etc.)
    return image, {}

def auto_orient(image):
    """Auto-orients an image based on EXIF data."""
    return ImageOps.exif_transpose(image), {}

def grayscale(image):
    """Converts an image to grayscale."""
    return ImageOps.grayscale(image).convert('RGB'), {}

def transform_annotations(annotations, transform_params, original_width, original_height):
    """Transforms annotations to match the preprocessed image."""
    # This is a simplified example. Real implementation needs to handle
    # different annotation types (bboxes, polygons, etc.) and transformations.
    if not annotations or not transform_params:
        return annotations

    output_width = transform_params.get('output_width', original_width)
    output_height = transform_params.get('output_height', original_height)
    
    scale_x = output_width / original_width
    scale_y = output_height / original_height

    new_annotations = []
    for ann in annotations:
        new_result = []
        for r in ann.get('result', []):
            value = r.get('value', {})
            if 'x' in value and 'y' in value and 'width' in value and 'height' in value:
                value['x'] *= scale_x
                value['y'] *= scale_y
                value['width'] *= scale_x
                value['height'] *= scale_y
            new_result.append(r)
        new_annotations.append({'id': ann['id'], 'result': new_result})
        
    return new_annotations

# 可用的预处理配置
AVAILABLE_PREPROCESSING = {
    'resize': {
        'name': '调整图像尺寸',
        'description': '将图像调整到指定尺寸',
        'function': resize_image,
        'params': {
            'width': {'type': 'int', 'default': 512, 'min': 32, 'max': 4096, 'description': '目标宽度'},
            'height': {'type': 'int', 'default': 512, 'min': 32, 'max': 4096, 'description': '目标高度'},
            'mode': {'type': 'select', 'default': 'stretch_to', 'options': ['stretch_to', 'fit_within', 'fill_crop'], 'description': '缩放模式'}
        }
    },
    'auto_orient': {
        'name': '自动旋转',
        'description': '根据 EXIF 数据自动调整图像方向',
        'function': auto_orient,
        'params': {}
    },
    'grayscale': {
        'name': '灰度化',
        'description': '将彩色图像转换为灰度图像',
        'function': grayscale,
        'params': {}
    }
}

PREPROCESSING_FUNCTIONS = {
    name: config['function'] for name, config in AVAILABLE_PREPROCESSING.items()
}