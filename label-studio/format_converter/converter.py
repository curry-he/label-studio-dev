#!/usr/bin/env python3
"""
格式转换脚本 - (参考源码重构版)

本脚本严格参考 label-studio-sdk.converter 的源码逻辑，重新实现了从
Label Studio 标注格式到 YOLO TXT 格式的转换过程。

它消除了对外部库 'Converter' 类的黑箱依赖，使得整个转换流程
完全透明、可控且易于调试。

主要步骤:
1.  遍历输入目录，加载所有JSON标注文件。
2.  从所有标注中自动发现并整理出一个全局的类别列表 (classes.txt)。
3.  遍历每个标注任务（图片），为其生成对应的YOLO格式的.txt标签文件。
4.  执行坐标转换：将Label Studio的百分比坐标(x, y, width, height)
    转换为YOLO要求的归一化中心点坐标(x_center, y_center, width, height)。
5.  将原始图片文件复制到输出目录。
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


def get_labels_from_tasks(tasks: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, int]]:
    """
    参考 _get_labels 方法的逻辑。
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
    
    sorted_labels = sorted(list(label_set))
    label_to_id = {label: i for i, label in enumerate(sorted_labels)}
    
    logger.info(f"Discovered {len(sorted_labels)} unique labels: {sorted_labels}")
    return sorted_labels, label_to_id


def convert_ls_to_yolo_bbox(value: Dict[str, float]) -> Tuple[float, float, float, float]:
    """
    参考 convert_annotation_to_yolo 的核心逻辑。
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


def process_and_export(input_dir: str, output_dir: str):
    """
    主处理函数，模拟 Converter.convert_to_yolo 的完整流程。
    """
    # --- 1. 创建输出目录结构 ---
    output_labels_dir = os.path.join(output_dir, 'labels')
    output_images_dir = os.path.join(output_dir, 'images')
    os.makedirs(output_labels_dir, exist_ok=True)
    os.makedirs(output_images_dir, exist_ok=True)
    logger.info("Created output directories: 'images/' and 'labels/'")

    # --- 2. 加载所有任务数据 ---
    all_tasks = []
    logger.info(f"Scanning for JSON files in '{input_dir}'...")
    for filename in os.listdir(input_dir):
        if filename.lower().endswith('.json'):
            filepath = os.path.join(input_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    all_tasks.append(json.load(f))
            except Exception as e:
                logger.error(f"Failed to load or parse {filename}: {e}")
    logger.info(f"Found and loaded {len(all_tasks)} annotation tasks.")

    if not all_tasks:
        logger.warning("No tasks found. Exiting.")
        return

    # --- 3. 生成类别文件 (classes.txt) ---
    sorted_labels, label_to_id = get_labels_from_tasks(all_tasks)
    if not sorted_labels:
        logger.error("Could not find any labels in the tasks. 'classes.txt' will not be generated.")
        return
        
    classes_path = os.path.join(output_dir, 'classes.txt')
    with open(classes_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sorted_labels))
    logger.info(f"'classes.txt' created successfully at '{classes_path}'")

    # --- 4. 迭代处理每个任务，生成 YOLO 标签文件 ---
    labels_generated_count = 0
    images_copied_count = 0
    for task in all_tasks:
        image_s3_path = task.get('data', {}).get('image', '')
        if not image_s3_path:
            logger.warning(f"Task with ID {task.get('id', 'N/A')} has no image path. Skipping.")
            continue
        
        image_filename = os.path.basename(image_s3_path)
        base_filename = os.path.splitext(image_filename)[0]
        
        # 复制图片文件
        source_image_path = os.path.join(input_dir, image_filename)
        if os.path.exists(source_image_path):
            shutil.copy(source_image_path, output_images_dir)
            images_copied_count += 1
        else:
            logger.warning(f"Image '{image_filename}' not found in input directory. Label file might be generated without a corresponding image.")

        # 生成标签文件路径
        label_filepath = os.path.join(output_labels_dir, f"{base_filename}.txt")
        yolo_lines = []
        
        annotations = task.get('annotations') or task.get('completions', [])
        if annotations:
            latest_annotation = annotations[-1]
            for result in latest_annotation.get('result', []):
                # 检查是否是矩形框标注，并且有原始尺寸信息
                if result.get('type') == 'rectanglelabels' and 'original_width' in result:
                    label_name = result['value']['rectanglelabels'][0]
                    class_id = label_to_id[label_name]
                    
                    x_c, y_c, w, h = convert_ls_to_yolo_bbox(result['value'])
                    
                    yolo_lines.append(f"{class_id} {x_c:.6f} {y_c:.6f} {w:.6f} {h:.6f}")
        
        # 写入.txt文件，即使没有标注也要创建一个空文件
        with open(label_filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(yolo_lines))
        
        if yolo_lines:
            labels_generated_count += 1
            
    logger.info(f"Processing complete. Copied {images_copied_count} images.")
    logger.info(f"Generated {len(all_tasks)} label files, of which {labels_generated_count} contain annotations.")


def main():
    parser = argparse.ArgumentParser(
        description="A robust, source-code-referenced script to convert Label Studio annotations to YOLO format.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--input_dir', default='/pfs/augmented_dataset', help='Input directory with images and LS JSON files.')
    parser.add_argument('--output_dir', default='/pfs/out', help='Output directory for the YOLO dataset.')
    
    # 保留这些参数以兼容Pachyderm流水线定义，但脚本内部不再使用它们
    parser.add_argument('--format', help=argparse.SUPPRESS)
    parser.add_argument('--work_dir', help=argparse.SUPPRESS)
    
    args = parser.parse_args()

    logger.info("--- Starting Label Studio to YOLO Conversion ---")
    logger.info(f"Input Directory: {args.input_dir}")
    logger.info(f"Output Directory: {args.output_dir}")

    try:
        process_and_export(args.input_dir, args.output_dir)
        logger.info("--- Conversion finished successfully! ---")
    except Exception as e:
        logger.error(f"An unexpected error occurred during conversion: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

