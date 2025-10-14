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
                
                # 计算边界框坐标
                x_min = abs_x
                y_min = abs_y
                x_max = abs_x + abs_w
                y_max = abs_y + abs_h
                
                # 边界框验证和修正
                x_min = max(0, min(x_min, img_width - 1))
                y_min = max(0, min(y_min, img_height - 1))
                x_max = max(x_min + 1, min(x_max, img_width))
                y_max = max(y_min + 1, min(y_max, img_height))
                
                # 确保边界框有效（宽度和高度 > 0）
                if x_max > x_min and y_max > y_min:
                    bboxes.append([x_min, y_min, x_max, y_max])
                    class_labels.append(value.get('rectanglelabels', ['unknown'])[0])
                    print(f"添加边界框: [{x_min:.1f}, {y_min:.1f}, {x_max:.1f}, {y_max:.1f}]")
                else:
                    print(f"跳过无效边界框: [{x_min:.1f}, {y_min:.1f}, {x_max:.1f}, {y_max:.1f}]")
                    
            elif annotation_type == 'keypointlabels' and 'x' in value and 'y' in value:
                # 处理关键点标注
                x, y = value['x'], value['y']
                # 从百分比转换为绝对像素值
                abs_x = x * img_width / 100
                abs_y = y * img_height / 100
                
                # 关键点验证和修正
                abs_x = max(0, min(abs_x, img_width - 1))
                abs_y = max(0, min(abs_y, img_height - 1))
                
                # albumentation期望 [x, y, visibility] visibility=2表示可见
                keypoints.append([abs_x, abs_y, 2])
                keypoint_labels.append(value.get('keypointlabels', ['unknown'])[0])
                print(f"添加关键点: [{abs_x:.1f}, {abs_y:.1f}]")

    print(f"总共处理 {len(bboxes)} 个有效边界框, {len(keypoints)} 个关键点")

    try:
        # 不设置固定随机种子，让变换真正随机
        
        # Create the transformation pipeline
        if bboxes and keypoints:
            # 同时有边界框和关键点
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
                transformed_labels = transformed['class_labels']
                transformed_keypoints = transformed['keypoints']
                transformed_keypoint_labels = transformed['keypoint_labels']
                print(f"变换后剩余 {len(transformed_bboxes)} 个边界框, {len(transformed_keypoints)} 个关键点")
            except Exception as mixed_error:
                print(f"混合标注变换失败: {mixed_error}")
                print("回退到仅图像变换模式")
                transform = A.Compose(transform_pipeline)
                transformed = transform(image=image_np)
                transformed_bboxes = []
                transformed_labels = []
                transformed_keypoints = []
                transformed_keypoint_labels = []
        elif bboxes:
            # 只有边界框
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
                transformed_labels = transformed['class_labels']
                transformed_keypoints = []
                transformed_keypoint_labels = []
                print(f"变换后剩余 {len(transformed_bboxes)} 个边界框")
            except Exception as bbox_error:
                print(f"边界框变换失败: {bbox_error}")
                print("回退到仅图像变换模式")
                transform = A.Compose(transform_pipeline)
                transformed = transform(image=image_np)
                transformed_bboxes = []
                transformed_labels = []
                transformed_keypoints = []
                transformed_keypoint_labels = []
        elif keypoints:
            # 只有关键点
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
                transformed_labels = []
                transformed_keypoints = transformed['keypoints']
                transformed_keypoint_labels = transformed['keypoint_labels']
                print(f"变换后剩余 {len(transformed_keypoints)} 个关键点")
            except Exception as keypoint_error:
                print(f"关键点变换失败: {keypoint_error}")
                print("回退到仅图像变换模式")
                transform = A.Compose(transform_pipeline)
                transformed = transform(image=image_np)
                transformed_bboxes = []
                transformed_labels = []
                transformed_keypoints = []
                transformed_keypoint_labels = []
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
        
        # 返回增强参数
        aug_params = {
            'applied_augmentations': applied_augmentations,
            'num_augmentations': len(transform_pipeline)
        }
        
        return transformed_image, new_annotations, aug_params
        
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