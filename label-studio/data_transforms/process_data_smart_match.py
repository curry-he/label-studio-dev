import os
import json
import time
import random
from PIL import Image
from preprocessing import apply_preprocessing
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

def split_dataset(matched_pairs, split_config):
    """
    根据配置划分数据集
    split_config: {"enabled": True, "train": 70, "test": 20, "valid": 10}
    返回: {"train": [...], "test": [...], "valid": [...]}
    """
    if not split_config.get("enabled", False):
        return {"all": matched_pairs}
    
    print(f"\n=== 开始数据集划分 ===")
    print(f"总数据量: {len(matched_pairs)}")
    
    # 随机打乱数据，确保随机性
    shuffled_pairs = matched_pairs.copy()
    random.shuffle(shuffled_pairs)
    
    # 获取划分比例
    train_ratio = split_config.get("train", 70) / 100.0
    test_ratio = split_config.get("test", 20) / 100.0
    valid_ratio = split_config.get("valid", 10) / 100.0
    
    # 计算各个分割的数量
    total = len(shuffled_pairs)
    train_count = int(total * train_ratio)
    test_count = int(total * test_ratio)
    valid_count = total - train_count - test_count  # 剩余全部给valid，避免舍入误差
    
    # 划分数据
    splits = {
        "train": shuffled_pairs[:train_count],
        "test": shuffled_pairs[train_count:train_count + test_count],
        "valid": shuffled_pairs[train_count + test_count:]
    }
    
    print(f"划分结果:")
    print(f"  🚂 Train: {len(splits['train'])} 样本 ({len(splits['train'])/total*100:.1f}%)")
    print(f"  🧪 Test:  {len(splits['test'])} 样本 ({len(splits['test'])/total*100:.1f}%)")
    print(f"  ✅ Valid: {len(splits['valid'])} 样本 ({len(splits['valid'])/total*100:.1f}%)")
    
    return splits

def process_split(split_name, matched_pairs, config, output_base_dir):
    """
    处理单个数据集分割 - 使用子目录结构
    """
    print(f"\n=== 处理 {split_name.upper()} 分割 ===")
    print(f"样本数量: {len(matched_pairs)}")
    
    # 创建分割子目录（即使为空也创建）
    split_dir = os.path.join(output_base_dir, split_name)
    os.makedirs(split_dir, exist_ok=True)
    print(f"创建分割目录: {split_dir}")
    
    if not matched_pairs:
        print(f"⚠️ {split_name} 分割为空，但已创建目录结构")
        return 0, 0
    
    processed_count = 0
    error_count = 0
    
    for image_path, annotation_path, annotation_data in matched_pairs:
        try:
            print(f"处理: {os.path.basename(image_path)} -> {split_name}/")
            
            # 从标注数据中提取annotations
            annotations = annotation_data.get('annotations', [])
            
            # 加载和处理图片
            with Image.open(image_path) as img:
                original_width, original_height = img.size

                # 应用预处理 (使用新的albumentation接口)
                processed_img, processed_annotations, pp_params = apply_preprocessing(
                    img, annotations, config.get('preprocessing', [])
                )
                
                # 应用数据增强
                augmentation_config = config.get('augmentation', [])
                if augmentation_config:
                    final_img, final_annotations, aug_params = apply_augmentation(
                        processed_img, processed_annotations, augmentation_config
                    )
                else:
                    final_img = processed_img
                    final_annotations = processed_annotations
                    aug_params = {}

                # 保持原始文件名，输出到分割子目录
                original_filename = os.path.basename(image_path)
                output_image_path = os.path.join(split_dir, original_filename)

                # 保存处理后的图片
                final_img.save(output_image_path)
                print(f"💾 保存图片: {split_name}/{original_filename}")

                # 保存变换后的标注
                if annotations:
                    output_annotation_filename = f"{os.path.splitext(original_filename)[0]}.json"
                    output_annotation_path = os.path.join(split_dir, output_annotation_filename)
                    
                    # 保存完整的标注数据结构，保持Label Studio格式
                    output_annotation_data = {
                        **annotation_data,
                        'annotations': final_annotations,
                        'processed_at': time.time(),
                        'processing_config': config,
                        'split': split_name  # 添加分割信息
                    }
                    
                    with open(output_annotation_path, 'w', encoding='utf-8') as f:
                        json.dump(output_annotation_data, f, indent=2, ensure_ascii=False)
                    print(f"💾 保存标注: {split_name}/{output_annotation_filename}")
                
                processed_count += 1
                
        except Exception as e:
            print(f"❌ 处理 {os.path.basename(image_path)} 失败: {e}")
            error_count += 1
            import traceback
            traceback.print_exc()
    
    print(f"{split_name.upper()} 分割处理完成: 成功 {processed_count}, 失败 {error_count}")
    print(f"输出目录: {split_dir}")
    return processed_count, error_count

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
    
    # 扫描标注文件（过滤掉配置文件目录）
    annotations = []
    if os.path.exists("/pfs/annotations"):
        for root, dirs, files in os.walk("/pfs/annotations"):
            # 过滤掉配置文件目录
            if "_processing_config" in root:
                print(f"跳过配置目录: {root}")
                continue
                
            for file in files:
                if file.lower().endswith('.json'):
                    # 额外检查：跳过明显的配置文件
                    if file.lower() in ['config.json', 'processing_config.json']:
                        print(f"跳过配置文件: {file}")
                        continue
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
    智能匹配模式的数据处理主函数 (支持数据集划分)
    """
    start_time = time.time()
    output_dir = "/pfs/out"
    os.makedirs(output_dir, exist_ok=True)
    
    print("=== 智能匹配模式数据处理 (支持数据集划分) ===")
    print(f"输出目录: {output_dir}")
    
    # 查找配置文件 - 支持新的配置文件位置
    config_path = None
    config_locations = [
        "/pfs/annotations/_processing_config/config.json",  # 新位置（优先）
        "/pfs/annotations/config.json",  # 旧位置（向后兼容）
        "/pfs/raw_images/config.json"   # 最旧位置（向后兼容）
    ]
    
    for potential_config in config_locations:
        if os.path.exists(potential_config):
            try:
                with open(potential_config, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                config_path = potential_config
                print(f"✅ 成功加载配置文件: {config_path}")
                print(f"配置内容: {json.dumps(config, indent=2, ensure_ascii=False)}")
                break
            except Exception as e:
                print(f"⚠️ 配置文件读取失败 {potential_config}: {e}")
                continue
    
    if not config_path:
        print("⚠️ 未找到有效的config.json，使用默认配置")
        config = {}

    # 智能匹配图片和标注
    matched_pairs = find_matching_pairs()
    
    if not matched_pairs:
        print("❌ 没有找到匹配的图片-标注对，无法继续处理")
        return
    
    # 检查是否启用数据集划分
    split_config = config.get('split', {'enabled': False})
    
    if split_config.get('enabled', False):
        print(f"\n🎯 启用数据集划分模式")
        # 设置随机种子确保可重现性
        if 'random_seed' in config:
            random.seed(config['random_seed'])
            print(f"使用随机种子: {config['random_seed']}")
        
        # 执行数据集划分
        splits = split_dataset(matched_pairs, split_config)
        
        # 处理每个分割
        total_processed = 0
        total_errors = 0
        split_results = {}
        
        for split_name, split_pairs in splits.items():
            if split_name != 'all':  # 跳过未启用划分时的'all'键
                processed, errors = process_split(split_name, split_pairs, config, output_dir)
                total_processed += processed
                total_errors += errors
                split_results[split_name] = {
                    'total': len(split_pairs),
                    'processed': processed,
                    'errors': errors
                }
        
        # 创建包含划分信息的处理报告
        processing_report = {
            "version_id": config.get('version_id'),
            "project_id": config.get('project_id'),
            "intelligent_matching": True,
            "dataset_split_enabled": True,
            "split_config": split_config,
            "split_results": split_results,
            "total_matched_pairs": len(matched_pairs),
            "total_processed": total_processed,
            "total_errors": total_errors,
            "preprocessing_steps": config.get('preprocessing', []),
            "augmentation_steps": config.get('augmentation', []),
            "processing_time": time.time() - start_time,
            "status": "completed" if total_errors == 0 else "completed_with_errors"
        }
        
    else:
        print(f"\n📁 使用传统处理模式 (不划分数据集)")
        # 传统处理方式 - 向后兼容
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

                    # 应用预处理 (使用新的albumentation接口)
                    processed_img, processed_annotations, pp_params = apply_preprocessing(
                        img, annotations, config.get('preprocessing', [])
                    )
                    if pp_params:
                        print(f"预处理参数: {pp_params}")
                    
                    # 应用数据增强
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

                    # 确定输出路径
                    original_filename = os.path.basename(image_path)
                    output_image_path = os.path.join(output_dir, original_filename)

                    # 保存处理后的图片
                    final_img.save(output_image_path)
                    print(f"💾 保存图片: {output_image_path}")

                    # 保存变换后的标注
                    if annotations:
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

        # 创建传统模式的处理报告
        processing_report = {
            "version_id": config.get('version_id'),
            "project_id": config.get('project_id'),
            "intelligent_matching": True,
            "dataset_split_enabled": False,
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
    if split_config.get('enabled', False):
        print(f"划分模式: 启用")
        for split_name, result in processing_report.get('split_results', {}).items():
            print(f"  {split_name.upper()}: {result['processed']}/{result['total']} 成功")
        print(f"总计: 成功 {processing_report['total_processed']}, 失败 {processing_report['total_errors']}")
    else:
        print(f"传统模式: 成功 {processing_report.get('processed_pairs', 0)}, 失败 {processing_report.get('failed_pairs', 0)}")
    print(f"处理耗时: {time.time() - start_time:.2f}秒")
    print(f"输出目录: {output_dir}")

if __name__ == "__main__":
    main()