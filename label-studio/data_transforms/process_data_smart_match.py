import os
import json
import time
from PIL import Image
from preprocessing import apply_preprocessing, transform_annotations as transform_annotations_preprocessing
from augmentation import apply_augmentation
import glob

def extract_original_filename(annotation_data):
    """
    从Label Studio标注数据中提取原始图片文件名
    """
    try:
        image_path = annotation_data.get('data', {}).get('image', '')
        if image_path:
            # 从 s3://master.raw_images.default/1731985516.0423145.jpg 中提取文件名
            filename = image_path.split('/')[-1]
            return filename
    except Exception as e:
        print(f"提取文件名失败: {e}")
    return None

def find_matching_pairs():
    """
    基于标注数据中的原始文件名信息，匹配图片和标注文件
    """
    print("=== 开始智能匹配图片和标注文件 ===")
    
    # 扫描原始图片
    raw_images = []
    if os.path.exists("/pfs/raw_images"):
        for root, dirs, files in os.walk("/pfs/raw_images"):
            for file in files:
                if file.lower().endswith(('.jpg', '.png', '.jpeg')):
                    raw_images.append(os.path.join(root, file))
    
    print(f"找到 {len(raw_images)} 个图片文件:")
    for img in raw_images[:5]:  # 显示前5个
        print(f"  📸 {os.path.basename(img)}")
    
    # 扫描标注文件
    annotations = []
    if os.path.exists("/pfs/annotations"):
        for root, dirs, files in os.walk("/pfs/annotations"):
            for file in files:
                if file.lower().endswith('.json'):
                    annotations.append(os.path.join(root, file))
    
    print(f"找到 {len(annotations)} 个标注文件:")
    for ann in annotations[:5]:  # 显示前5个
        print(f"  📝 {os.path.basename(ann)}")
    
    # 建立匹配关系
    matched_pairs = []
    for ann_path in annotations:
        try:
            with open(ann_path, 'r', encoding='utf-8') as f:
                ann_data = json.load(f)
                
            # 提取原始文件名
            original_filename = extract_original_filename(ann_data)
            
            if not original_filename:
                print(f"⚠️ 无法从 {os.path.basename(ann_path)} 中提取原始文件名")
                continue
                
            # 查找匹配的图片
            matched_img = None
            for img_path in raw_images:
                if os.path.basename(img_path) == original_filename:
                    matched_img = img_path
                    break
            
            if matched_img:
                matched_pairs.append((matched_img, ann_path, ann_data))
                print(f"✅ 匹配成功: {os.path.basename(matched_img)} <-> {os.path.basename(ann_path)}")
            else:
                print(f"❌ 未找到匹配图片: {original_filename} (来自 {os.path.basename(ann_path)})")
                
        except Exception as e:
            print(f"❌ 处理标注文件 {ann_path} 时出错: {e}")
    
    print(f"=== 匹配完成，共找到 {len(matched_pairs)} 个有效图片-标注对 ===")
    return matched_pairs

def main():
    """
    智能匹配模式的数据处理主函数
    """
    start_time = time.time()
    output_dir = "/pfs/out"
    os.makedirs(output_dir, exist_ok=True)
    
    print("=== 智能匹配模式数据处理 ===")
    print(f"输出目录: {output_dir}")
    
    # 查找配置文件
    config_path = None
    config_dirs = ["/pfs/raw_images", "/pfs/annotations"]
    for search_dir in config_dirs:
        if os.path.exists(search_dir):
            potential_config = os.path.join(search_dir, 'config.json')
            if os.path.exists(potential_config):
                config_path = potential_config
                break
    
    if not config_path:
        print("⚠️ 未找到config.json，使用默认配置")
        config = {}
    else:
        print(f"📋 读取配置文件: {config_path}")
        with open(config_path, 'r') as f:
            config = json.load(f)
        print(f"配置内容: {json.dumps(config, indent=2)}")

    # 智能匹配图片和标注
    matched_pairs = find_matching_pairs()
    
    if not matched_pairs:
        print("❌ 没有找到匹配的图片-标注对，无法继续处理")
        return
    
    # 处理每个匹配对
    processed_count = 0
    error_count = 0
    
    for image_path, annotation_path, annotation_data in matched_pairs:
        try:
            print(f"\n--- 处理: {os.path.basename(image_path)} ---")
            
            # 从标注数据中提取annotations
            annotations = annotation_data.get('annotations', [])
            print(f"包含 {len(annotations)} 条标注")
            
            # 加载和处理图片
            with Image.open(image_path) as img:
                original_width, original_height = img.size
                print(f"原始尺寸: {original_width}x{original_height}")

                # 应用预处理
                processed_img, pp_params = apply_preprocessing(img, config.get('preprocessing', []))
                if pp_params:
                    print(f"预处理参数: {pp_params}")
                
                # 应用数据增强
                augmentation_config = config.get('augmentation', [])
                if augmentation_config:
                    processed_img, transformed_annotations, aug_params = apply_augmentation(
                        processed_img, annotations, augmentation_config
                    )
                    if aug_params:
                        print(f"增强参数: {aug_params}")
                else:
                    transformed_annotations = annotations
                    aug_params = {}

                # 确定输出路径
                original_filename = os.path.basename(image_path)
                output_image_path = os.path.join(output_dir, original_filename)

                # 保存处理后的图片
                processed_img.save(output_image_path)
                print(f"💾 保存图片: {output_image_path}")

                # 转换和保存标注
                if annotations:
                    transform_params = {**pp_params, **aug_params}
                    final_annotations = transform_annotations_preprocessing(
                        transformed_annotations, transform_params, original_width, original_height
                    )
                    
                    output_annotation_path = os.path.join(output_dir, 
                        os.path.splitext(original_filename)[0] + '.json')
                    
                    # 保存完整的标注数据结构，保持Label Studio格式
                    output_annotation_data = {
                        **annotation_data,
                        'annotations': final_annotations,
                        'processed_at': time.time(),
                        'processing_config': config
                    }
                    
                    with open(output_annotation_path, 'w', encoding='utf-8') as f:
                        json.dump(output_annotation_data, f, indent=2, ensure_ascii=False)
                    print(f"💾 保存标注: {output_annotation_path}")
                
                processed_count += 1
                
        except Exception as e:
            print(f"❌ 处理 {os.path.basename(image_path)} 失败: {e}")
            error_count += 1
            import traceback
            traceback.print_exc()

    # 创建处理报告
    processing_report = {
        "version_id": config.get('version_id'),
        "project_id": config.get('project_id'),
        "intelligent_matching": True,
        "total_images_found": len([p[0] for p in matched_pairs]),
        "total_annotations_found": len([p[1] for p in matched_pairs]),
        "matched_pairs": len(matched_pairs),
        "processed_pairs": processed_count,
        "failed_pairs": error_count,
        "preprocessing_steps": config.get('preprocessing', []),
        "augmentation_steps": config.get('augmentation', []),
        "processing_time": time.time() - start_time,
        "status": "completed" if error_count == 0 else "completed_with_errors"
    }
    
    # 生成唯一的处理报告文件名（避免Cross input重复文件冲突）
    import hashlib
    datum_hash = hashlib.md5(f"{len(matched_pairs)}_{start_time}".encode()).hexdigest()[:8]
    report_filename = f'processing_report_{datum_hash}.json'
    report_path = os.path.join(output_dir, report_filename)
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(processing_report, f, indent=2, ensure_ascii=False)
    print(f"\n📊 处理报告保存至: {report_path}")
    
    print(f"\n=== 智能匹配处理完成 ===")
    print(f"找到匹配对: {len(matched_pairs)}")
    print(f"成功处理: {processed_count}")
    print(f"处理失败: {error_count}")
    print(f"处理耗时: {time.time() - start_time:.2f}秒")
    print(f"输出目录: {output_dir}")

if __name__ == "__main__":
    main()