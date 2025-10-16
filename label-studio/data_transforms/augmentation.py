from PIL import Image
import albumentations as A
import numpy as np
from label_sync_utils import AnnotationTracker

def apply_augmentation(image, annotations, config):
    """应用数据增强（改进版 - 使用AnnotationTracker）"""
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

    # 使用AnnotationTracker
    tracker = AnnotationTracker(annotations)
    bboxes, class_labels, keypoints, keypoint_labels = tracker.extract_annotations_for_albumentations(
        img_width, img_height
    )

    try:
        # 创建变换（增强阶段可以过滤部分标注）
        if bboxes and keypoints:
            try:
                transform = A.Compose(
                    transform_pipeline,
                    bbox_params=A.BboxParams(
                        format='pascal_voc',
                        label_fields=['class_labels'],
                        min_area=1.0,
                        min_visibility=0.1
                    ),
                    keypoint_params=A.KeypointParams(
                        format='xy',
                        label_fields=['keypoint_labels'],
                        remove_invisible=False
                    )
                )
                print(f"应用变换到 {len(bboxes)} 个边界框和 {len(keypoints)} 个关键点")
                transformed = transform(
                    image=image_np,
                    bboxes=bboxes,
                    class_labels=class_labels,
                    keypoints=keypoints,
                    keypoint_labels=keypoint_labels
                )
                transformed_bboxes = transformed['bboxes']
                transformed_keypoints = transformed['keypoints']
                print(f"变换后剩余 {len(transformed_bboxes)} 个边界框, {len(transformed_keypoints)} 个关键点")
            except Exception as mixed_error:
                print(f"混合标注变换失败: {mixed_error}")
                print("回退到仅图像变换模式")
                transform = A.Compose(transform_pipeline)
                transformed = transform(image=image_np)
                transformed_bboxes = []
                transformed_keypoints = []

        elif bboxes:
            try:
                transform = A.Compose(
                    transform_pipeline,
                    bbox_params=A.BboxParams(
                        format='pascal_voc',
                        label_fields=['class_labels'],
                        min_area=1.0,
                        min_visibility=0.1
                    )
                )
                print(f"应用变换到 {len(bboxes)} 个边界框")
                transformed = transform(image=image_np, bboxes=bboxes, class_labels=class_labels)
                transformed_bboxes = transformed['bboxes']
                transformed_keypoints = []
                print(f"变换后剩余 {len(transformed_bboxes)} 个边界框")
            except Exception as bbox_error:
                print(f"边界框变换失败: {bbox_error}")
                print("回退到仅图像变换模式")
                transform = A.Compose(transform_pipeline)
                transformed = transform(image=image_np)
                transformed_bboxes = []
                transformed_keypoints = []

        elif keypoints:
            try:
                transform = A.Compose(
                    transform_pipeline,
                    keypoint_params=A.KeypointParams(
                        format='xy',
                        label_fields=['keypoint_labels'],
                        remove_invisible=False
                    )
                )
                print(f"应用变换到 {len(keypoints)} 个关键点")
                transformed = transform(image=image_np, keypoints=keypoints, keypoint_labels=keypoint_labels)
                transformed_bboxes = []
                transformed_keypoints = transformed['keypoints']
                print(f"变换后剩余 {len(transformed_keypoints)} 个关键点")
            except Exception as keypoint_error:
                print(f"关键点变换失败: {keypoint_error}")
                print("回退到仅图像变换模式")
                transform = A.Compose(transform_pipeline)
                transformed = transform(image=image_np)
                transformed_bboxes = []
                transformed_keypoints = []

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

        # 返回增强参数
        aug_params = {
            'applied_augmentations': applied_augmentations,
            'num_augmentations': len(transform_pipeline)
        }

        return transformed_image, new_annotations, aug_params

    except Exception as e:
        print(f"数据增强失败: {e}")
        import traceback
        traceback.print_exc()
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
