"""
标注验证工具 - 确保处理后的标注与图片一致
"""
import logging
from PIL import Image, ImageDraw, ImageFont
import json
import os

logger = logging.getLogger(__name__)


def validate_annotation_coordinates(image_path, annotation_data):
    """
    验证标注坐标是否在图片范围内

    :param image_path: 图片路径
    :param annotation_data: Label Studio标注数据
    :return: (is_valid, issues)
    """
    issues = []

    try:
        with Image.open(image_path) as img:
            img_width, img_height = img.size
    except Exception as e:
        logger.error(f"无法打开图片 {image_path}: {e}")
        return False, [f"无法打开图片: {e}"]

    for ann in annotation_data.get('annotations', []):
        for r in ann.get('result', []):
            value = r.get('value', {})
            annotation_type = r.get('type')
            annotation_id = r.get('id', 'unknown')

            if annotation_type == 'rectanglelabels':
                x, y, w, h = value.get('x', 0), value.get('y', 0), value.get('width', 0), value.get('height', 0)

                # 检查边界
                if x < 0 or y < 0:
                    issues.append(f"BBox {annotation_id}: 负坐标 x={x:.2f}%, y={y:.2f}%")
                if w <= 0 or h <= 0:
                    issues.append(f"BBox {annotation_id}: 无效尺寸 w={w:.2f}%, h={h:.2f}%")
                if x + w > 100.1 or y + h > 100.1:  # 允许0.1%的浮点误差
                    issues.append(f"BBox {annotation_id}: 超出边界 x+w={x+w:.2f}%, y+h={y+h:.2f}%")

            elif annotation_type == 'keypointlabels':
                x, y = value.get('x', 0), value.get('y', 0)
                parent_id = r.get('parentID')

                if x < 0 or y < 0 or x > 100.1 or y > 100.1:  # 允许0.1%的浮点误差
                    issues.append(f"KeyPoint {annotation_id}: 超出边界 x={x:.2f}%, y={y:.2f}%")

                # 检查parentID是否存在
                if parent_id:
                    parent_exists = False
                    for ann2 in annotation_data.get('annotations', []):
                        for r2 in ann2.get('result', []):
                            if r2.get('id') == parent_id and r2.get('type') == 'rectanglelabels':
                                parent_exists = True
                                break
                        if parent_exists:
                            break
                    if not parent_exists:
                        issues.append(f"KeyPoint {annotation_id}: 父BBox {parent_id} 不存在")

    is_valid = len(issues) == 0

    if not is_valid:
        logger.warning(f"标注验证失败 {os.path.basename(image_path)}:")
        for issue in issues:
            logger.warning(f"  - {issue}")

    return is_valid, issues


def visualize_annotations(image_path, annotation_data, output_path):
    """
    可视化标注（用于调试）

    在图片上绘制bbox和keypoint，保存用于人工检查
    """
    try:
        with Image.open(image_path) as img:
            # 转换为RGB模式（以防是灰度图）
            if img.mode != 'RGB':
                img = img.convert('RGB')

            img_width, img_height = img.size
            draw = ImageDraw.Draw(img)

            # 尝试使用默认字体
            try:
                font = ImageFont.truetype("arial.ttf", 12)
            except:
                font = ImageFont.load_default()

            bbox_count = 0
            keypoint_count = 0

            for ann in annotation_data.get('annotations', []):
                for r in ann.get('result', []):
                    value = r.get('value', {})
                    annotation_type = r.get('type')

                    if annotation_type == 'rectanglelabels':
                        x, y, w, h = value['x'], value['y'], value['width'], value['height']

                        # 转换为像素坐标
                        x1 = x * img_width / 100
                        y1 = y * img_height / 100
                        x2 = (x + w) * img_width / 100
                        y2 = (y + h) * img_height / 100

                        # 绘制bbox
                        draw.rectangle([x1, y1, x2, y2], outline='red', width=2)
                        label = value.get('rectanglelabels', ['unknown'])[0]

                        # 绘制标签背景
                        text_bbox = draw.textbbox((x1, y1 - 15), label, font=font)
                        draw.rectangle(text_bbox, fill='red')
                        draw.text((x1, y1 - 15), label, fill='white', font=font)

                        bbox_count += 1

                    elif annotation_type == 'keypointlabels':
                        x, y = value['x'], value['y']

                        # 转换为像素坐标
                        x_px = x * img_width / 100
                        y_px = y * img_height / 100

                        # 绘制keypoint
                        radius = 5
                        draw.ellipse([x_px-radius, y_px-radius, x_px+radius, y_px+radius],
                                   fill='green', outline='green')

                        keypoint_count += 1

            # 在图片左上角显示统计信息
            stats_text = f"BBoxes: {bbox_count}, KeyPoints: {keypoint_count}"
            stats_bbox = draw.textbbox((10, 10), stats_text, font=font)
            draw.rectangle(stats_bbox, fill='black')
            draw.text((10, 10), stats_text, fill='white', font=font)

            img.save(output_path)
            logger.info(f"保存可视化结果: {output_path} (BBoxes: {bbox_count}, KeyPoints: {keypoint_count})")

    except Exception as e:
        logger.error(f"可视化标注失败 {image_path}: {e}")
        import traceback
        traceback.print_exc()


def validate_and_visualize_batch(split_dir, output_validation_dir=None):
    """
    批量验证和可视化一个数据集分割

    :param split_dir: 数据集分割目录（包含图片和JSON标注）
    :param output_validation_dir: 可视化输出目录（可选）
    :return: (total_count, valid_count, invalid_count)
    """
    if output_validation_dir:
        os.makedirs(output_validation_dir, exist_ok=True)

    total_count = 0
    valid_count = 0
    invalid_count = 0

    # 查找所有JSON文件
    json_files = [f for f in os.listdir(split_dir) if f.endswith('.json') and not f.startswith('processing_report')]

    for json_file in json_files:
        json_path = os.path.join(split_dir, json_file)

        # 推断对应的图片文件
        base_name = os.path.splitext(json_file)[0]
        image_file = None
        for ext in ['.jpg', '.jpeg', '.png', '.bmp']:
            potential_image = os.path.join(split_dir, base_name + ext)
            if os.path.exists(potential_image):
                image_file = potential_image
                break

        if not image_file:
            logger.warning(f"找不到对应的图片文件: {json_file}")
            continue

        # 加载标注数据
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                annotation_data = json.load(f)
        except Exception as e:
            logger.error(f"加载标注文件失败 {json_path}: {e}")
            continue

        # 验证
        is_valid, issues = validate_annotation_coordinates(image_file, annotation_data)
        total_count += 1

        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1

            # 如果验证失败，生成可视化
            if output_validation_dir:
                vis_path = os.path.join(output_validation_dir, os.path.basename(image_file))
                visualize_annotations(image_file, annotation_data, vis_path)

    logger.info(f"批量验证完成: 总计 {total_count}, 有效 {valid_count}, 无效 {invalid_count}")
    return total_count, valid_count, invalid_count
