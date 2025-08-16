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

PREPROCESSING_FUNCTIONS = {
    'resize': resize_image,
    'auto_orient': auto_orient,
    'grayscale': grayscale,
}