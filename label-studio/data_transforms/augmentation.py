from PIL import Image
import albumentations as A
import numpy as np

def apply_augmentation(image, annotations, config):
    """Applies a series of augmentations to an image and its annotations."""
    if not config:
        return image, annotations, {}

    transform_pipeline = []
    for step in config:
        func = AUGMENTATION_FUNCTIONS.get(step['type'])
        if func:
            transform_pipeline.append(func(**step.get('params', {})))

    if not transform_pipeline:
        return image, annotations, {}

    # Convert PIL image to numpy array for Albumentations
    image_np = np.array(image)

    # Convert Label Studio annotations to Albumentations format
    bboxes = []
    class_labels = []
    for ann in annotations:
        for r in ann.get('result', []):
            value = r.get('value', {})
            if 'x' in value and 'y' in value and 'width' in value and 'height' in value:
                x, y, w, h = value['x'], value['y'], value['width'], value['height']
                # Convert from percentage to absolute pixel values
                abs_x = x * image.width / 100
                abs_y = y * image.height / 100
                abs_w = w * image.width / 100
                abs_h = h * image.height / 100
                
                # Albumentations expects [x_min, y_min, x_max, y_max]
                bboxes.append([abs_x, abs_y, abs_x + abs_w, abs_y + abs_h])
                class_labels.append(value.get('rectanglelabels', ['unknown'])[0])

    # Create the transformation pipeline
    transform = A.Compose(
        transform_pipeline,
        bbox_params=A.BboxParams(format='pascal_voc', label_fields=['class_labels'])
    )

    # Apply the transformation
    transformed = transform(image=image_np, bboxes=bboxes, class_labels=class_labels)
    
    transformed_image = Image.fromarray(transformed['image'])
    transformed_bboxes = transformed['bboxes']

    # Convert transformed bboxes back to Label Studio format
    new_annotations = []
    # This part needs to be carefully implemented to match the original annotation structure
    # and update the 'result' with the new coordinates.
    
    return transformed_image, new_annotations, {}


# --- Albumentations-based augmentation functions ---

def flip(p=0.5, direction='horizontal'):
    if direction == 'vertical':
        return A.VerticalFlip(p=p)
    return A.HorizontalFlip(p=p)

def rotate(limit=90, p=0.5):
    return A.Rotate(limit=limit, p=p)

def brightness_contrast(brightness_limit=0.2, contrast_limit=0.2, p=0.5):
    return A.RandomBrightnessContrast(brightness_limit=brightness_limit, contrast_limit=contrast_limit, p=p)

def gaussian_noise(var_limit=(10.0, 50.0), p=0.5):
    return A.GaussNoise(var_limit=var_limit, p=p)


AUGMENTATION_FUNCTIONS = {
    'flip': flip,
    'rotate': rotate,
    'brightness_contrast': brightness_contrast,
    'gaussian_noise': gaussian_noise,
}