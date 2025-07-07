"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license.
"""
import logging

logger = logging.getLogger(__name__)

def auto_orient(image, **kwargs):
    """
    Auto-orients an image based on its EXIF data.
    :param image: PIL Image object
    :return: PIL Image object
    """
    # Placeholder for auto-orient logic
    logger.info("Applying auto-orient...")
    # from PIL import ImageOps
    # return ImageOps.exif_transpose(image)
    return image

def resize(image, width, height, **kwargs):
    """
    Resizes an image to the specified width and height.
    :param image: PIL Image object
    :param width: Target width
    :param height: Target height
    :return: PIL Image object
    """
    # Placeholder for resize logic
    logger.info(f"Resizing image to {width}x{height}...")
    # return image.resize((width, height))
    return image

# Register all available preprocessing functions
AVAILABLE_PREPROCESSING = {
    "auto_orient": {
        "name": "Auto-Orient",
        "description": "Automatically adjusts image orientation based on EXIF data.",
        "function": auto_orient,
        "params": []
    },
    "resize": {
        "name": "Resize",
        "description": "Resize the image to a specific size.",
        "function": resize,
        "params": [
            {"name": "width", "type": "number", "default": 640},
            {"name": "height", "type": "number", "default": 640}
        ]
    }
}

def get_preprocessing_params(name):
    return AVAILABLE_PREPROCESSING.get(name, {}).get('params', [])

def apply_preprocessing(image, config):
    """
    Apply a series of preprocessing steps to an image.
    :param image: PIL Image object
    :param config: List of preprocessing steps
    :return: A tuple of (Processed PIL Image object, dict of applied transformations)
    """
    applied_params = {}
    for step in config:
        func_name = step.get('name')
        params = step.get('params', {})
        if func_name in AVAILABLE_PREPROCESSING:
            func = AVAILABLE_PREPROCESSING[func_name]['function']
            image = func(image, **params)
            if func_name == 'resize':
                applied_params['resize'] = params
    return image, applied_params


def transform_annotations(annotations, transform_params, original_width, original_height):
    """
    Transform annotations based on the applied image transformations.
    :param annotations: List of annotation results (from Annotation.result)
    :param transform_params: Dictionary of applied transformations and their params
    :param original_width: Original image width
    :param original_height: Original image height
    :return: Transformed annotations
    """
    new_annotations = []
    resize_params = transform_params.get('resize')

    if not resize_params:
        return annotations  # No transformation to apply

    new_width = resize_params.get('width')
    new_height = resize_params.get('height')

    if not new_width or not new_height:
        return annotations

    width_ratio = new_width / original_width
    height_ratio = new_height / original_height

    for result in annotations:
        if result.get('type') == 'rectanglelabels':
            value = result.get('value', {})
            x = value.get('x', 0) * width_ratio
            y = value.get('y', 0) * height_ratio
            width = value.get('width', 0) * width_ratio
            height = value.get('height', 0) * height_ratio

            result['value']['x'] = x
            result['value']['y'] = y
            result['value']['width'] = width
            result['value']['height'] = height
        
        new_annotations.append(result)

    return new_annotations