#!/usr/bin/env python3
"""
格式转换脚本 - 支持数据集分割的 YOLO 转换器

本脚本支持两种输入格式：
1. 传统格式：所有文件在输入目录根部
2. 分割格式：文件组织在 train/test/valid 子目录中

对于分割格式，会自动生成分层的 YOLO 输出结构。
"""
import os
import sys
import json
import argparse
import logging
import shutil
from typing import List, Dict, Any, Set, Tuple

# --- 初始化日志记录器 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)


def detect_dataset_splits(input_dir: str) -> List[str]:
    """
    检测输入目录是否包含数据集分割子目录
    返回发现的分割列表，如 ['train', 'test', 'valid']
    """
    potential_splits = ['train', 'test', 'valid']
    found_splits = []
    
    for split in potential_splits:
        split_dir = os.path.join(input_dir, split)
        if os.path.exists(split_dir) and os.path.isdir(split_dir):
            # 即使目录为空也包含在分割中（为了保持结构一致性）
            files = os.listdir(split_dir)
            has_images = any(f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')) for f in files)
            has_annotations = any(f.lower().endswith('.json') for f in files)
            
            # 包含目录（无论是否为空）
            found_splits.append(split)
            if has_images or has_annotations:
                logger.info(f"检测到数据集分割: {split} (包含 {len(files)} 个文件)")
            else:
                logger.info(f"检测到数据集分割: {split} (目录为空)")
    
    return found_splits


def get_labels_from_tasks(tasks: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, int]]:
    """
    从所有任务中扫描并提取出一个唯一的、排序后的标签类别列表。
    """
    label_set: Set[str] = set()
    for task in tasks:
        # 兼容 LS 的两种标注 key: 'annotations' (新) 和 'completions' (旧)
        annotations = task.get('annotations') or task.get('completions', [])
        if not annotations:
            continue

        # 我们只关心最新的、未被取消的标注
        latest_annotation = annotations[-1]
        if latest_annotation.get('was_cancelled') or latest_annotation.get('skipped'):
            continue

        for result in latest_annotation.get('result', []):
            if result.get('type') == 'rectanglelabels':
                labels = result.get('value', {}).get('rectanglelabels', [])
                label_set.update(labels)
            # 关键点标签不应被视为独立的类别，因此注释掉
            # elif result.get('type') == 'keypointlabels':
            #     labels = result.get('value', {}).get('keypointlabels', [])
            #     label_set.update(labels)
    
    sorted_labels = sorted(list(label_set))
    label_to_id = {label: i for i, label in enumerate(sorted_labels)}
    
    logger.info(f"发现 {len(sorted_labels)} 个唯一标签: {sorted_labels}")
    return sorted_labels, label_to_id


def convert_ls_to_yolo_bbox(value: Dict[str, float]) -> Tuple[float, float, float, float]:
    """
    将 Label Studio 的 bbox value 转换为 YOLO 格式的归一化坐标。
    LS value: {'x': %, 'y': %, 'width': %, 'height': %}
    YOLO format: [x_center, y_center, width, height] (0-1 normalized)
    """
    x, y, w, h = value['x'], value['y'], value['width'], value['height']

    # LS 坐标是百分比，直接除以100即可完成归一化
    x_center = (x + w / 2) / 100.0
    y_center = (y + h / 2) / 100.0
    width = w / 100.0
    height = h / 100.0

    return x_center, y_center, width, height


def convert_ls_to_yolo_keypoint(value: Dict[str, float]) -> Tuple[float, float]:
    """
    将 Label Studio 的 keypoint value 转换为 YOLO 格式的归一化坐标。
    LS value: {'x': %, 'y': %, 'width': %} (width表示关键点的尺寸)
    YOLO keypoint format: [x, y] (0-1 normalized)
    """
    x, y = value['x'], value['y']
    
    # LS 坐标是百分比，直接除以100即可完成归一化
    x_norm = x / 100.0
    y_norm = y / 100.0
    
    return x_norm, y_norm


def process_split_directory(split_name: str, split_dir: str, output_base_dir: str,
                          sorted_labels: List[str], label_to_id: Dict[str, int]):
    """
    处理单个数据集分割目录
    """
    logger.info(f"处理 {split_name} 分割...")
    
    split_output_dir = os.path.join(output_base_dir, split_name)
    split_images_dir = os.path.join(split_output_dir, 'images')
    split_labels_dir = os.path.join(split_output_dir, 'labels')
    
    os.makedirs(split_images_dir, exist_ok=True)
    os.makedirs(split_labels_dir, exist_ok=True)
    
    classes_path = os.path.join(split_output_dir, 'classes.txt')
    with open(classes_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sorted_labels))
    logger.info(f"创建 {split_name}/classes.txt")
    
    all_tasks = []
    json_files = [f for f in os.listdir(split_dir) if f.lower().endswith('.json') and not f.startswith('processing_report')]
    
    if not json_files:
        logger.info(f"{split_name} 分割目录为空，创建空的结构")
        return 0, 0
    
    for filename in json_files:
        filepath = os.path.join(split_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                all_tasks.append(json.load(f))
        except Exception as e:
            logger.error(f"加载 {filename} 失败: {e}")
    
    logger.info(f"{split_name} 分割: 加载了 {len(all_tasks)} 个标注任务")
    
    labels_generated_count = 0
    images_copied_count = 0
    
    for task in all_tasks:
        image_s3_path = task.get('data', {}).get('image', '')
        if not image_s3_path:
            logger.warning(f"任务 {task.get('id', 'N/A')} 没有图片路径，跳过")
            continue
        
        image_filename = os.path.basename(image_s3_path)
        base_filename = os.path.splitext(image_filename)[0]
        
        source_image_path = os.path.join(split_dir, image_filename)
        if os.path.exists(source_image_path):
            target_image_path = os.path.join(split_images_dir, image_filename)
            shutil.copy(source_image_path, target_image_path)
            images_copied_count += 1
        else:
            logger.warning(f"图片 {image_filename} 在 {split_name} 目录中未找到")

        label_filepath = os.path.join(split_labels_dir, f"{base_filename}.txt")
        yolo_lines = []
        
        annotations = task.get('annotations') or task.get('completions', [])
        if annotations:
            latest_annotation = annotations[-1]
            results = latest_annotation.get('result', [])
            
            # --- 新逻辑：基于parentID关联bbox和keypoints ---
            
            # 1. 提取所有bboxes，用它们的ID作为索引
            bboxes = {}
            for r in results:
                if r.get('type') == 'rectanglelabels' and 'id' in r:
                    label_name = r['value']['rectanglelabels'][0]
                    class_id = label_to_id.get(label_name)
                    if class_id is None: continue
                    
                    x_c, y_c, w, h = convert_ls_to_yolo_bbox(r['value'])
                    bboxes[r['id']] = {
                        'class_id': class_id,
                        'bbox': [x_c, y_c, w, h],
                        'keypoints': []
                    }

            # 2. 提取所有keypoints，并根据parentID关联到bboxes
            for r in results:
                if r.get('type') == 'keypointlabels' and 'parentID' in r:
                    parent_id = r.get('parentID')
                    if parent_id in bboxes:
                        x_kp, y_kp = convert_ls_to_yolo_keypoint(r['value'])
                        # YOLO格式要求keypoint后面跟一个可见性标志
                        bboxes[parent_id]['keypoints'].extend([x_kp, y_kp, 2])

            # 3. 生成YOLO格式的行
            for bbox_id, data in bboxes.items():
                bbox_str = " ".join(f"{coord:.6f}" for coord in data['bbox'])
                kpts_str = " ".join(f"{coord:.6f}" for coord in data['keypoints'])
                
                line = f"{data['class_id']} {bbox_str}"
                if kpts_str:
                    line += f" {kpts_str}"
                yolo_lines.append(line)
        
        with open(label_filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(yolo_lines))
        
        if yolo_lines:
            labels_generated_count += 1
    
    logger.info(f"{split_name} 分割处理完成: 复制了 {images_copied_count} 张图片, "
                f"生成了 {len(all_tasks)} 个标签文件 (其中 {labels_generated_count} 个包含标注)")
    
    return images_copied_count, labels_generated_count


def process_traditional_format(input_dir: str, output_dir: str):
    """
    处理传统格式 (所有文件在根目录)
    """
    logger.info("使用传统处理模式 (所有文件在根目录)")
    
    # 创建输出目录结构
    output_labels_dir = os.path.join(output_dir, 'labels')
    output_images_dir = os.path.join(output_dir, 'images')
    os.makedirs(output_labels_dir, exist_ok=True)
    os.makedirs(output_images_dir, exist_ok=True)
    logger.info("创建输出目录: 'images/' 和 'labels/'")

    # 加载所有任务数据
    all_tasks = []
    logger.info(f"扫描 JSON 文件: '{input_dir}'...")
    for filename in os.listdir(input_dir):
        if filename.lower().endswith('.json') and not filename.startswith('processing_report'):
            filepath = os.path.join(input_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    all_tasks.append(json.load(f))
            except Exception as e:
                logger.error(f"加载 {filename} 失败: {e}")
    
    logger.info(f"加载了 {len(all_tasks)} 个标注任务")

    if not all_tasks:
        logger.warning("没有找到任务数据，退出")
        return

    # 生成类别文件 (classes.txt)
    sorted_labels, label_to_id = get_labels_from_tasks(all_tasks)
    if not sorted_labels:
        logger.error("无法找到任何标签，不会生成 'classes.txt'")
        return
        
    classes_path = os.path.join(output_dir, 'classes.txt')
    with open(classes_path, 'w', encoding='utf-8') as f:
        f.write('\\n'.join(sorted_labels))
    logger.info(f"创建 classes.txt: {classes_path}")

    # 处理每个任务，生成 YOLO 标签文件
    labels_generated_count = 0
    images_copied_count = 0
    
    for task in all_tasks:
        image_s3_path = task.get('data', {}).get('image', '')
        if not image_s3_path:
            logger.warning(f"任务 {task.get('id', 'N/A')} 没有图片路径，跳过")
            continue
        
        image_filename = os.path.basename(image_s3_path)
        base_filename = os.path.splitext(image_filename)[0]
        
        # 复制图片文件
        source_image_path = os.path.join(input_dir, image_filename)
        if os.path.exists(source_image_path):
            target_image_path = os.path.join(output_images_dir, image_filename)
            shutil.copy(source_image_path, target_image_path)
            images_copied_count += 1
        else:
            logger.warning(f"图片 {image_filename} 在输入目录中未找到")

        # 生成标签文件路径
        label_filepath = os.path.join(output_labels_dir, f"{base_filename}.txt")
        yolo_lines = []
        
        annotations = task.get('annotations') or task.get('completions', [])
        if annotations:
            latest_annotation = annotations[-1]
            results = latest_annotation.get('result', [])

            # --- 新逻辑：基于parentID关联bbox和keypoints ---
            
            # 1. 提取所有bboxes，用它们的ID作为索引
            bboxes = {}
            for r in results:
                if r.get('type') == 'rectanglelabels' and 'id' in r:
                    label_name = r['value']['rectanglelabels'][0]
                    class_id = label_to_id.get(label_name)
                    if class_id is None: continue

                    x_c, y_c, w, h = convert_ls_to_yolo_bbox(r['value'])
                    bboxes[r['id']] = {
                        'class_id': class_id,
                        'bbox': [x_c, y_c, w, h],
                        'keypoints': []
                    }

            # 2. 提取所有keypoints，并根据parentID关联到bboxes
            for r in results:
                if r.get('type') == 'keypointlabels' and 'parentID' in r:
                    parent_id = r.get('parentID')
                    if parent_id in bboxes:
                        x_kp, y_kp = convert_ls_to_yolo_keypoint(r['value'])
                        # YOLO格式要求keypoint后面跟一个可见性标志 (0=不可见, 1=遮挡, 2=可见)
                        bboxes[parent_id]['keypoints'].extend([x_kp, y_kp, 2])

            # 3. 生成YOLO格式的行
            for bbox_id, data in bboxes.items():
                bbox_str = " ".join(f"{coord:.6f}" for coord in data['bbox'])
                kpts_str = " ".join(f"{coord:.6f}" for coord in data['keypoints'])
                
                line = f"{data['class_id']} {bbox_str}"
                if kpts_str:
                    line += f" {kpts_str}"
                yolo_lines.append(line)
        
        # 写入.txt文件，即使没有标注也要创建一个空文件
        with open(label_filepath, 'w', encoding='utf-8') as f:
            f.write('\\n'.join(yolo_lines))
        
        if yolo_lines:
            labels_generated_count += 1
            
    logger.info(f"传统模式处理完成: 复制了 {images_copied_count} 张图片")
    logger.info(f"生成了 {len(all_tasks)} 个标签文件，其中 {labels_generated_count} 个包含标注")


def process_and_export(input_dir: str, output_dir: str):
    """
    主处理函数，自动检测输入格式并相应处理
    """
    logger.info(f"开始处理: 输入={input_dir}, 输出={output_dir}")
    
    # 检测是否有数据集分割
    detected_splits = detect_dataset_splits(input_dir)
    
    if detected_splits:
        logger.info(f"检测到数据集分割: {detected_splits}")
        logger.info("使用分割处理模式")
        
        # 收集所有分割的任务数据以生成全局标签
        all_tasks = []
        for split_name in detected_splits:
            split_dir = os.path.join(input_dir, split_name)
            for filename in os.listdir(split_dir):
                if filename.lower().endswith('.json') and not filename.startswith('processing_report'):
                    filepath = os.path.join(split_dir, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            all_tasks.append(json.load(f))
                    except Exception as e:
                        logger.error(f"加载 {filepath} 失败: {e}")
        
        # 生成全局标签列表
        sorted_labels, label_to_id = get_labels_from_tasks(all_tasks)
        if not sorted_labels:
            logger.error("无法从任何分割中找到标签")
            return
        
        # 处理每个分割
        total_images = 0
        total_labels = 0
        
        for split_name in detected_splits:
            split_dir = os.path.join(input_dir, split_name)
            images_count, labels_count = process_split_directory(
                split_name, split_dir, output_dir, sorted_labels, label_to_id
            )
            total_images += images_count
            total_labels += labels_count
        
        logger.info(f"所有分割处理完成: 总计 {total_images} 张图片, {total_labels} 个包含标注的文件")
        
        # 创建总体信息文件
        dataset_info = {
            "format": "YOLO",
            "splits": detected_splits,
            "total_classes": len(sorted_labels),
            "classes": sorted_labels,
            "total_images": total_images,
            "total_labels_with_annotations": total_labels
        }
        
        info_path = os.path.join(output_dir, "dataset_info.json")
        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(dataset_info, f, indent=2, ensure_ascii=False)
        logger.info(f"创建数据集信息文件: {info_path}")
        
    else:
        logger.info("未检测到数据集分割，使用传统处理模式")
        process_traditional_format(input_dir, output_dir)


def main():
    parser = argparse.ArgumentParser(
        description="支持数据集分割的 Label Studio 到 YOLO 转换器",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--input_dir', default='/pfs/augmented_dataset', 
                       help='输入目录，可包含分割子目录或传统格式')
    parser.add_argument('--output_dir', default='/pfs/out', 
                       help='输出目录，将生成 YOLO 格式数据集')
    
    # 保留这些参数以兼容Pachyderm流水线定义
    parser.add_argument('--format', help=argparse.SUPPRESS)
    parser.add_argument('--work_dir', help=argparse.SUPPRESS)
    
    args = parser.parse_args()

    logger.info("=== 开始 Label Studio 到 YOLO 转换 (支持数据集分割) ===")
    logger.info(f"输入目录: {args.input_dir}")
    logger.info(f"输出目录: {args.output_dir}")

    try:
        process_and_export(args.input_dir, args.output_dir)
        logger.info("=== 转换成功完成! ===")
    except Exception as e:
        logger.error(f"转换过程中发生错误: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

