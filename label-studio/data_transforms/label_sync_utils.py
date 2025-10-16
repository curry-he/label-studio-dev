"""
标签同步工具 - 确保albumentations变换后标签正确对应
"""
import logging
from typing import Dict, List, Tuple, Any
import copy

logger = logging.getLogger(__name__)


class AnnotationTracker:
    """
    标注追踪器 - 维护变换前后的标注对应关系
    """
    def __init__(self, annotations):
        """
        初始化追踪器

        :param annotations: Label Studio原始标注列表
        """
        self.original_annotations = copy.deepcopy(annotations)
        self.id_to_bbox_mapping = {}
        self.id_to_keypoint_mapping = {}
        self.bbox_order = []
        self.keypoint_order = []

    def extract_annotations_for_albumentations(self, img_width, img_height):
        """
        提取标注并转换为albumentation格式，同时记录映射关系

        :return: (bboxes, class_labels, keypoints, keypoint_labels)
        """
        bboxes = []
        class_labels = []
        keypoints = []
        keypoint_labels = []

        for ann in self.original_annotations:
            for r in ann.get('result', []):
                value = r.get('value', {})
                annotation_type = r.get('type')
                annotation_id = r.get('id')

                if annotation_type == 'rectanglelabels':
                    if 'x' in value and 'y' in value and 'width' in value and 'height' in value:
                        x, y, w, h = value['x'], value['y'], value['width'], value['height']

                        # 转换为绝对像素坐标
                        abs_x = x * img_width / 100
                        abs_y = y * img_height / 100
                        abs_w = w * img_width / 100
                        abs_h = h * img_height / 100

                        # albumentation格式 [x_min, y_min, x_max, y_max]
                        bbox = [abs_x, abs_y, abs_x + abs_w, abs_y + abs_h]
                        label = value.get('rectanglelabels', ['unknown'])[0]

                        # 记录映射关系
                        bbox_idx = len(bboxes)
                        self.id_to_bbox_mapping[bbox_idx] = {
                            'annotation_id': annotation_id,
                            'original_result': copy.deepcopy(r),
                            'ann_idx': self.original_annotations.index(ann)
                        }
                        self.bbox_order.append(annotation_id)

                        bboxes.append(bbox)
                        class_labels.append(label)

                        logger.debug(f"BBox {bbox_idx}: id={annotation_id}, label={label}")

                elif annotation_type == 'keypointlabels':
                    if 'x' in value and 'y' in value:
                        x, y = value['x'], value['y']

                        # 转换为绝对像素坐标
                        abs_x = x * img_width / 100
                        abs_y = y * img_height / 100

                        # albumentation格式 [x, y, visibility]
                        keypoint = [abs_x, abs_y, 2]
                        label = value.get('keypointlabels', ['unknown'])[0]
                        parent_id = r.get('parentID')

                        # 记录映射关系
                        kp_idx = len(keypoints)
                        self.id_to_keypoint_mapping[kp_idx] = {
                            'annotation_id': annotation_id,
                            'parent_id': parent_id,
                            'original_result': copy.deepcopy(r),
                            'ann_idx': self.original_annotations.index(ann)
                        }
                        self.keypoint_order.append(annotation_id)

                        keypoints.append(keypoint)
                        keypoint_labels.append(label)

                        logger.debug(f"KeyPoint {kp_idx}: id={annotation_id}, parent={parent_id}, label={label}")

        logger.info(f"提取了 {len(bboxes)} 个bbox, {len(keypoints)} 个keypoint")
        return bboxes, class_labels, keypoints, keypoint_labels

    def rebuild_annotations_from_transformed(self, transformed_bboxes, transformed_keypoints,
                                            new_img_width, new_img_height):
        """
        从albumentation变换后的结果重建Label Studio标注

        :param transformed_bboxes: 变换后的bboxes
        :param transformed_keypoints: 变换后的keypoints
        :param new_img_width: 变换后的图片宽度
        :param new_img_height: 变换后的图片高度
        :return: 更新后的annotations
        """
        # 深拷贝原始标注
        new_annotations = copy.deepcopy(self.original_annotations)

        # 标记哪些bbox和keypoint被保留了
        kept_bbox_ids = set()
        kept_keypoint_ids = set()

        # 处理变换后的bboxes
        for new_idx, transformed_bbox in enumerate(transformed_bboxes):
            if new_idx in self.id_to_bbox_mapping:
                mapping = self.id_to_bbox_mapping[new_idx]
                annotation_id = mapping['annotation_id']
                ann_idx = mapping['ann_idx']

                # 找到对应的result
                for r in new_annotations[ann_idx]['result']:
                    if r.get('id') == annotation_id and r.get('type') == 'rectanglelabels':
                        # 更新坐标（转换回百分比）
                        x_min, y_min, x_max, y_max = transformed_bbox

                        new_x = (x_min / new_img_width) * 100
                        new_y = (y_min / new_img_height) * 100
                        new_width = ((x_max - x_min) / new_img_width) * 100
                        new_height = ((y_max - y_min) / new_img_height) * 100

                        # 验证坐标合法性
                        if new_x < 0 or new_y < 0 or new_width <= 0 or new_height <= 0:
                            logger.warning(f"BBox {annotation_id} 变换后坐标无效: "
                                         f"x={new_x:.2f}, y={new_y:.2f}, w={new_width:.2f}, h={new_height:.2f}")
                            continue

                        r['value']['x'] = new_x
                        r['value']['y'] = new_y
                        r['value']['width'] = new_width
                        r['value']['height'] = new_height

                        kept_bbox_ids.add(annotation_id)
                        logger.debug(f"更新BBox {annotation_id}: "
                                   f"x={new_x:.2f}%, y={new_y:.2f}%, w={new_width:.2f}%, h={new_height:.2f}%")
                        break

        # 处理变换后的keypoints
        for new_idx, transformed_keypoint in enumerate(transformed_keypoints):
            if new_idx in self.id_to_keypoint_mapping:
                mapping = self.id_to_keypoint_mapping[new_idx]
                annotation_id = mapping['annotation_id']
                parent_id = mapping['parent_id']
                ann_idx = mapping['ann_idx']

                # 检查父bbox是否仍然存在
                if parent_id and parent_id not in kept_bbox_ids:
                    logger.warning(f"KeyPoint {annotation_id} 的父BBox {parent_id} 被过滤，跳过此keypoint")
                    continue

                # 找到对应的result
                for r in new_annotations[ann_idx]['result']:
                    if r.get('id') == annotation_id and r.get('type') == 'keypointlabels':
                        # 更新坐标（转换回百分比）
                        x_abs, y_abs, visibility = transformed_keypoint

                        new_x = (x_abs / new_img_width) * 100
                        new_y = (y_abs / new_img_height) * 100

                        # 验证坐标合法性
                        if new_x < 0 or new_y < 0 or new_x > 100 or new_y > 100:
                            logger.warning(f"KeyPoint {annotation_id} 变换后坐标无效: "
                                         f"x={new_x:.2f}%, y={new_y:.2f}%")
                            continue

                        r['value']['x'] = new_x
                        r['value']['y'] = new_y

                        kept_keypoint_ids.add(annotation_id)
                        logger.debug(f"更新KeyPoint {annotation_id}: x={new_x:.2f}%, y={new_y:.2f}%")
                        break

        # 过滤掉被删除的标注
        filtered_annotations = []
        for ann in new_annotations:
            filtered_results = []
            for r in ann.get('result', []):
                annotation_id = r.get('id')
                annotation_type = r.get('type')

                if annotation_type == 'rectanglelabels' and annotation_id in kept_bbox_ids:
                    filtered_results.append(r)
                elif annotation_type == 'keypointlabels' and annotation_id in kept_keypoint_ids:
                    filtered_results.append(r)
                elif annotation_type not in ['rectanglelabels', 'keypointlabels']:
                    # 保留其他类型的标注
                    filtered_results.append(r)

            if filtered_results:
                ann['result'] = filtered_results
                filtered_annotations.append(ann)

        logger.info(f"保留了 {len(kept_bbox_ids)} 个bbox, {len(kept_keypoint_ids)} 个keypoint")

        if len(kept_bbox_ids) < len(self.id_to_bbox_mapping):
            logger.warning(f"⚠️ {len(self.id_to_bbox_mapping) - len(kept_bbox_ids)} 个bbox在变换中被过滤")

        if len(kept_keypoint_ids) < len(self.id_to_keypoint_mapping):
            logger.warning(f"⚠️ {len(self.id_to_keypoint_mapping) - len(kept_keypoint_ids)} 个keypoint在变换中被过滤")

        return filtered_annotations
