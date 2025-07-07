"""This file and its contents are licensed under the Apache License 2.0. Please see the included NOTICE for copyright information and LICENSE for a copy of the license.
"""
import logging

logger = logging.getLogger(__name__)

def flip(image, direction='horizontal', **kwargs):
    """
    Flips an image horizontally or vertically.
    :param image: PIL Image object
    :param direction: 'horizontal' or 'vertical'
    :return: PIL Image object
    """
    # Placeholder for flip logic
    logger.info(f"Flipping image {direction}...")
    # if direction == 'horizontal':
    #     return image.transpose(Image.FLIP_LEFT_RIGHT)
    # elif direction == 'vertical':
    #     return image.transpose(Image.FLIP_TOP_BOTTOM)
    return image

def rotate(image, angle=90, **kwargs):
    """
    Rotates an image by a specified angle.
    :param image: PIL Image object
    :param angle: Angle of rotation
    :return: PIL Image object
    """
    # Placeholder for rotate logic
    logger.info(f"Rotating image by {angle} degrees...")
    # return image.rotate(angle, expand=True)
    return image

# Register all available augmentation functions
AVAILABLE_AUGMENTATION = {
    "flip": {
        "name": "Flip",
        "description": "Flip the image horizontally or vertically.",
        "function": flip,
        "params": [
            {"name": "direction", "type": "string", "default": "horizontal", "options": ["horizontal", "vertical"]}
        ]
    },
    "rotate": {
        "name": "Rotate",
        "description": "Rotate the image by a certain angle.",
        "function": rotate,
        "params": [
            {"name": "angle", "type": "number", "default": 90}
        ]
    }
}

def get_augmentation_params(name):
    return AVAILABLE_AUGMENTATION.get(name, {}).get('params', [])

def apply_augmentation(image, config):
    """
    Apply a series of augmentation steps to an image.
    :param image: PIL Image object
    :param config: List of augmentation steps
    :return: A tuple of (Processed PIL Image object, dict of applied transformations)
    """
    applied_params = {}
    for step in config:
        func_name = step.get('name')
        params = step.get('params', {})
        if func_name in AVAILABLE_AUGMENTATION:
            func = AVAILABLE_AUGMENTATION[func_name]['function']
            image = func(image, **params)
            applied_params[func_name] = params
    return image, applied_params