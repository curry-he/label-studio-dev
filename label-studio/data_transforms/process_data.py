import os
import json
import time
from PIL import Image
# 假设 preprocessing 和 augmentation 模块在同一目录或可导入路径中
from preprocessing import apply_preprocessing
from augmentation import apply_augmentation
import glob # 导入 glob 模块用于更灵活的文件匹配

def main():
    """
    根据dev_plan.txt中的JOIN输入模式处理数据
    期望的输入结构:
    /pfs/raw_images/ - 原始图片仓库 (Source Cloud Storage)
    /pfs/annotations/ - 标注文件仓库 (Target Cloud Storage) 
    只处理同时存在图片和标注的文件（JOIN结果）
    
    JOIN逻辑：
    /pfs/raw_images/cat.jpg + /pfs/annotations/cat.json -> 处理
    /pfs/raw_images/dog.jpg + /pfs/annotations/dog.json -> 处理  
    /pfs/raw_images/bird.jpg + 无对应标注 -> 忽略
    """
    start_time = time.time()
    pfs_root = "/pfs"
    output_dir = "/pfs/out"
    os.makedirs(output_dir, exist_ok=True)
    
    # 根据JOIN模式，查找图片和标注目录
    raw_images_dir = "/pfs/raw_images"  
    annotations_dir = "/pfs/annotations"
    
    print(f"=== JOIN输入模式数据处理 ===")
    print(f"Raw images dir: {raw_images_dir}")
    print(f"Annotations dir: {annotations_dir}")
    print(f"Output dir: {output_dir}")
    
    # 检查JOIN输入目录是否存在
    if not os.path.exists(raw_images_dir):
        print(f"ERROR: Raw images directory not found: {raw_images_dir}")
        print("Available directories in /pfs:")
        for item in os.listdir(pfs_root):
            item_path = os.path.join(pfs_root, item)
            if os.path.isdir(item_path):
                print(f"  {item}/")
        return
        
    if not os.path.exists(annotations_dir):
        print(f"ERROR: Annotations directory not found: {annotations_dir}")
        print("Available directories in /pfs:")
        for item in os.listdir(pfs_root):
            item_path = os.path.join(pfs_root, item)
            if os.path.isdir(item_path):
                print(f"  {item}/")
        return

    # 查找配置文件（可能在任一输入目录中）
    config_path = None
    for search_dir in [raw_images_dir, annotations_dir]:
        potential_config = os.path.join(search_dir, 'config.json')
        if os.path.exists(potential_config):
            config_path = potential_config
            break
    
    if not config_path:
        print("ERROR: config.json not found in any input directory")
        print("Searching in:")
        print(f"  {raw_images_dir}")
        print(f"  {annotations_dir}")
        return

    print(f"Reading config from: {config_path}")
    
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    print(f"Config loaded: {json.dumps(config, indent=2)}")

    # 获取原始图片文件列表
    raw_image_files = []
    for root, dirs, files in os.walk(raw_images_dir):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                raw_image_files.append(os.path.join(root, file))
    
    print(f"Found {len(raw_image_files)} raw images")
    
    # 获取标注文件列表
    annotation_files = []
    for root, dirs, files in os.walk(annotations_dir):
        for file in files:
            if file.lower().endswith('.json'):
                annotation_files.append(os.path.join(root, file))
    
    print(f"Found {len(annotation_files)} annotation files")
    
    # 执行JOIN逻辑 - 只处理同时存在图片和标注的文件
    processed_count = 0
    error_count = 0
    joined_pairs = []
    
    for image_path in raw_image_files:
        # 从图片路径提取文件名（不含扩展名）作为JOIN键
        image_basename = os.path.splitext(os.path.basename(image_path))[0]
        
        # 在标注目录中寻找对应的标注文件
        matching_annotation = None
        for ann_path in annotation_files:
            ann_basename = os.path.splitext(os.path.basename(ann_path))[0]
            if ann_basename == image_basename:
                matching_annotation = ann_path
                break
        
        if matching_annotation:
            joined_pairs.append((image_path, matching_annotation))
            print(f"JOIN匹配: {os.path.basename(image_path)} <-> {os.path.basename(matching_annotation)}")
        else:
            print(f"跳过无标注图片: {os.path.basename(image_path)}")
    
    print(f"JOIN结果: {len(joined_pairs)} 个图片-标注对将被处理")
    
    # 处理每个JOIN匹配的图片-标注对
    for image_path, annotation_path in joined_pairs:
        try:
            print(f"\n--- 处理: {os.path.basename(image_path)} ---")
            
            # 加载标注数据
            annotations = []
            try:
                with open(annotation_path, 'r', encoding='utf-8') as f:
                    annotations = json.load(f)
                print(f"加载标注: {len(annotations) if isinstance(annotations, list) else 1} 条")
            except Exception as e:
                print(f"警告: 无法加载标注文件 {annotation_path}: {e}")
                annotations = []
            
            # 加载和处理图片
            with Image.open(image_path) as img:
                original_width, original_height = img.size
                print(f"原始图片尺寸: {original_width}x{original_height}")

                # 应用预处理 (使用新的albumentation接口)
                processed_img, processed_annotations, pp_params = apply_preprocessing(
                    img, annotations, config.get('preprocessing', [])
                )
                if pp_params:
                    print(f"预处理参数: {pp_params}")
                
                # 应用数据增强 (使用新的albumentation接口)
                augmentation_config = config.get('augmentation', [])
                if augmentation_config:
                    final_img, final_annotations, aug_params = apply_augmentation(
                        processed_img, processed_annotations, augmentation_config
                    )
                    if aug_params:
                        print(f"增强参数: {aug_params}")
                else:
                    final_img = processed_img
                    final_annotations = processed_annotations
                    aug_params = {}

                # 确定输出路径，保持相对目录结构
                rel_path = os.path.relpath(image_path, raw_images_dir)
                output_image_path = os.path.join(output_dir, rel_path)
                output_subdir = os.path.dirname(output_image_path)
                os.makedirs(output_subdir, exist_ok=True)

                # 保存处理后的图片
                final_img.save(output_image_path)
                print(f"保存图片: {output_image_path}")

                # 保存变换后的标注
                if final_annotations:
                    output_annotation_path = os.path.join(output_dir, 
                        os.path.splitext(rel_path)[0] + '.json')
                    with open(output_annotation_path, 'w', encoding='utf-8') as f:
                        json.dump(final_annotations, f, indent=2, ensure_ascii=False)
                    print(f"保存标注: {output_annotation_path}")
                
                processed_count += 1
                
        except Exception as e:
            print(f"错误: 处理 {os.path.basename(image_path)} 失败: {e}")
            error_count += 1
            import traceback
            traceback.print_exc()

    # 创建处理报告
    processing_report = {
        "version_id": config.get('version_id'),
        "project_id": config.get('project_id'),
        "join_mode": True,
        "total_raw_images": len(raw_image_files),
        "total_annotation_files": len(annotation_files),
        "joined_pairs": len(joined_pairs),
        "processed_images": processed_count,
        "failed_images": error_count,
        "preprocessing_steps": config.get('preprocessing', []),
        "augmentation_steps": config.get('augmentation', []),
        "processing_time": time.time() - start_time,
        "status": "completed" if error_count == 0 else "completed_with_errors"
    }
    
    # 保存处理报告
    report_path = os.path.join(output_dir, 'processing_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(processing_report, f, indent=2, ensure_ascii=False)
    print(f"\n处理报告保存至: {report_path}")
    
    print(f"\n=== 处理完成 ===")
    print(f"找到原始图片: {len(raw_image_files)}")
    print(f"找到标注文件: {len(annotation_files)}")  
    print(f"JOIN匹配对: {len(joined_pairs)}")
    print(f"成功处理: {processed_count}")
    print(f"处理失败: {error_count}")
    print(f"处理耗时: {time.time() - start_time:.2f}秒")
    print(f"输出目录: {output_dir}")

if __name__ == "__main__":
    main()