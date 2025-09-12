from PIL import Image
import albumentations as A
import numpy as np

def apply_augmentation(image, annotations, config):
    """Applies a series of augmentations to an image and its annotations."""
    if not config:
        return image, annotations, {}

    transform_pipeline = []
    applied_augmentations = []
    
    for step in config:
        func = AUGMENTATION_FUNCTIONS.get(step['type'])
        if func:
            aug_transform = func(**step.get('params', {}))
            transform_pipeline.append(aug_transform)
            applied_augmentations.append(f"{step['type']}: {step.get('params', {})}")

    if not transform_pipeline:
        return image, annotations, {}

    # Convert PIL image to numpy array for Albumentations
    image_np = np.array(image)
    img_height, img_width = image_np.shape[:2]

    # Convert Label Studio annotations to Albumentations format
    bboxes = []
    class_labels = []
    for ann in annotations:
        for r in ann.get('result', []):
            value = r.get('value', {})
            if 'x' in value and 'y' in value and 'width' in value and 'height' in value:
                x, y, w, h = value['x'], value['y'], value['width'], value['height']
                # Convert from percentage to absolute pixel values
                abs_x = x * img_width / 100
                abs_y = y * img_height / 100
                abs_w = w * img_width / 100
                abs_h = h * img_height / 100
                
                # Albumentations expects [x_min, y_min, x_max, y_max]
                bboxes.append([abs_x, abs_y, abs_x + abs_w, abs_y + abs_h])
                class_labels.append(value.get('rectanglelabels', ['unknown'])[0])

    try:
        # 为了确保一致的结果，设置随机种子
        np.random.seed(42)
        
        # Create the transformation pipeline
        if bboxes:
            transform = A.Compose(
                transform_pipeline,
                bbox_params=A.BboxParams(format='pascal_voc', label_fields=['class_labels'])
            )
            # Apply the transformation with bboxes
            transformed = transform(image=image_np, bboxes=bboxes, class_labels=class_labels)
        else:
            # No bboxes, apply transformation to image only
            transform = A.Compose(transform_pipeline)
            transformed = transform(image=image_np)
        
        transformed_image = Image.fromarray(transformed['image'])
        
        # Return augmentation parameters
        aug_params = {
            'applied_augmentations': applied_augmentations,
            'num_augmentations': len(transform_pipeline)
        }
        
        # For now, return original annotations (need proper bbox transformation)
        return transformed_image, annotations, aug_params
        
    except Exception as e:
        print(f"数据增强失败: {e}")
        return image, annotations, {}


# --- Albumentations-based augmentation functions ---

def flip(direction='horizontal', p=0.5):
    """创建水平或垂直翻转变换"""
    if direction == 'vertical':
        return A.VerticalFlip(p=p)
    return A.HorizontalFlip(p=p)

def rotate(limit=90, p=0.5):
    return A.Rotate(limit=limit, p=p)

def brightness_contrast(brightness_limit=0.2, contrast_limit=0.2, p=0.5):
    return A.RandomBrightnessContrast(brightness_limit=brightness_limit, contrast_limit=contrast_limit, p=p)

def gaussian_noise(var_limit=(10.0, 50.0), p=0.5):
    return A.GaussNoise(var_limit=var_limit, p=p)


# 可用的数据增强配置
AVAILABLE_AUGMENTATION = {
    'flip': {
        'name': '图像翻转',
        'description': '水平或垂直翻转图像',
        'function': flip,
        'params': {
            'p': {'type': 'float', 'default': 0.5, 'min': 0.0, 'max': 1.0, 'description': '应用概率'},
            'direction': {'type': 'select', 'default': 'horizontal', 'options': ['horizontal', 'vertical'], 'description': '翻转方向'}
        }
    },
    'rotate': {
        'name': '随机旋转',
        'description': '在指定角度范围内随机旋转图像',
        'function': rotate,
        'params': {
            'limit': {'type': 'int', 'default': 90, 'min': 1, 'max': 180, 'description': '最大旋转角度'},
            'p': {'type': 'float', 'default': 0.5, 'min': 0.0, 'max': 1.0, 'description': '应用概率'}
        }
    },
    'brightness_contrast': {
        'name': '亮度对比度调整',
        'description': '随机调整图像亮度和对比度',
        'function': brightness_contrast,
        'params': {
            'brightness_limit': {'type': 'float', 'default': 0.2, 'min': 0.0, 'max': 1.0, 'description': '亮度变化限制'},
            'contrast_limit': {'type': 'float', 'default': 0.2, 'min': 0.0, 'max': 1.0, 'description': '对比度变化限制'},
            'p': {'type': 'float', 'default': 0.5, 'min': 0.0, 'max': 1.0, 'description': '应用概率'}
        }
    },
    'gaussian_noise': {
        'name': '高斯噪声',
        'description': '向图像添加高斯噪声',
        'function': gaussian_noise,
        'params': {
            'var_limit': {'type': 'tuple', 'default': (10.0, 50.0), 'description': '噪声方差范围'},
            'p': {'type': 'float', 'default': 0.5, 'min': 0.0, 'max': 1.0, 'description': '应用概率'}
        }
    }
}

AUGMENTATION_FUNCTIONS = {
    name: config['function'] for name, config in AVAILABLE_AUGMENTATION.items()
}