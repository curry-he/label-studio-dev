"""
版本快照处理脚本

从version_snapshots仓库读取冻结的数据版本，应用预处理和增强，输出处理后的数据集。

关键特性：
1. 输入：版本快照（不可变数据）
2. 差异化增强：预处理应用于所有分割，增强仅应用于训练集
3. 标签同步：使用AnnotationTracker确保标签与图片变换同步
4. 验证机制：自动验证标注坐标并生成可视化
"""

import os
import json
import time
import random
from PIL import Image
from preprocessing import apply_preprocessing
from augmentation import apply_augmentation
from validate_annotations import validate_annotation_coordinates, visualize_annotations


def load_snapshot_metadata(snapshot_base_dir):
    """
    从快照目录加载元数据

    Args:
        snapshot_base_dir: 快照基础目录 (例如 /pfs/version_snapshots/project_1/version_1)

    Returns:
        dict: 元数据字典，包含config
    """
    metadata_path = os.path.join(snapshot_base_dir, "_metadata.json")

    if not os.path.exists(metadata_path):
        print(f"⚠️ 未找到元数据文件: {metadata_path}")
        return {}

    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        print(f"✅ 成功加载快照元数据")
        print(f"   版本ID: {metadata.get('version_id')}")
        print(f"   项目ID: {metadata.get('project_id')}")
        print(f"   创建时间: {metadata.get('created_at_str')}")
        print(f"   状态: {metadata.get('status')}")
        return metadata
    except Exception as e:
        print(f"❌ 加载元数据失败: {e}")
        return {}


def find_snapshot_data(snapshot_base_dir):
    """
    扫描快照目录，匹配图片和标注对

    快照结构:
    /project_X/version_Y/
        _metadata.json
        images/
            image1.jpg
            image2.jpg
        annotations/
            image1.json
            image2.json

    Args:
        snapshot_base_dir: 快照基础目录

    Returns:
        list: [(image_path, annotation_path, annotation_data), ...]
    """
    print(f"\n=== 扫描快照数据: {snapshot_base_dir} ===")

    images_dir = os.path.join(snapshot_base_dir, "images")
    annotations_dir = os.path.join(snapshot_base_dir, "annotations")

    if not os.path.exists(images_dir):
        print(f"❌ 图片目录不存在: {images_dir}")
        return []

    if not os.path.exists(annotations_dir):
        print(f"❌ 标注目录不存在: {annotations_dir}")
        return []

    # 扫描图片文件（构建文件名到路径的映射）
    image_files = {}
    for filename in os.listdir(images_dir):
        if filename.lower().endswith(('.jpg', '.png', '.jpeg')):
            image_path = os.path.join(images_dir, filename)
            # 使用完整文件名作为键，方便通过标注中的图片路径匹配
            image_files[filename] = image_path

    print(f"找到 {len(image_files)} 个图片文件")
    if image_files:
        print(f"图片列表示例: {list(image_files.keys())[:3]}")

    # 扫描标注文件并通过标注内容中的图片路径匹配
    matched_pairs = []
    for filename in os.listdir(annotations_dir):
        if filename.lower().endswith('.json'):
            annotation_path = os.path.join(annotations_dir, filename)

            try:
                with open(annotation_path, 'r', encoding='utf-8') as f:
                    annotation_data = json.load(f)

                # 从标注数据中提取图片路径
                image_url = annotation_data.get('data', {}).get('image', '')
                if image_url:
                    # 提取文件名（去掉URL前缀）
                    image_filename = image_url.split('/')[-1]

                    # 查找匹配的图片
                    if image_filename in image_files:
                        matched_pairs.append((
                            image_files[image_filename],
                            annotation_path,
                            annotation_data
                        ))
                        print(f"✅ 匹配: {filename} -> {image_filename}")
                    else:
                        print(f"⚠️ 标注 {filename} 引用的图片 {image_filename} 不存在")
                else:
                    print(f"⚠️ 标注 {filename} 中没有图片路径信息")

            except Exception as e:
                print(f"❌ 读取标注失败 {filename}: {e}")

    print(f"=== 共找到 {len(matched_pairs)} 个有效图片-标注对 ===")
    return matched_pairs


def split_dataset(matched_pairs, split_config):
    """
    根据配置划分数据集

    Args:
        matched_pairs: 图片-标注对列表
        split_config: {"enabled": True, "train": 70, "test": 20, "valid": 10}

    Returns:
        dict: {"train": [...], "test": [...], "valid": [...]}
    """
    if not split_config.get("enabled", False):
        return {"all": matched_pairs}

    print(f"\n=== 开始数据集划分 ===")
    print(f"总数据量: {len(matched_pairs)}")

    # 随机打乱数据
    shuffled_pairs = matched_pairs.copy()
    random.shuffle(shuffled_pairs)

    # 获取划分比例
    train_ratio = split_config.get("train", 70) / 100.0
    test_ratio = split_config.get("test", 20) / 100.0
    valid_ratio = split_config.get("valid", 10) / 100.0

    # 计算各分割数量
    total = len(shuffled_pairs)
    train_count = int(total * train_ratio)
    test_count = int(total * test_ratio)
    valid_count = total - train_count - test_count

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
    处理单个数据集分割 - 差异化增强和验证（支持增强倍数）

    预处理：应用于所有分割（train/test/valid）
    增强：仅应用于训练集（train），支持增强倍数生成多个变体

    Args:
        split_name: 分割名称 (train/test/valid)
        matched_pairs: 该分割的图片-标注对
        config: 处理配置
        output_base_dir: 输出根目录

    Returns:
        (processed_count, error_count, variant_count): 成功计数、失败计数、总变体数
    """
    print(f"\n=== 处理 {split_name.upper()} 分割 ===")
    print(f"原始样本数量: {len(matched_pairs)}")

    # 创建分割子目录
    split_dir = os.path.join(output_base_dir, split_name)
    os.makedirs(split_dir, exist_ok=True)
    print(f"创建分割目录: {split_dir}")

    # 创建验证目录
    validation_dir = os.path.join(output_base_dir, f"{split_name}_validation")
    os.makedirs(validation_dir, exist_ok=True)

    if not matched_pairs:
        print(f"⚠️ {split_name} 分割为空")
        return 0, 0, 0

    # 确定是否应用增强（仅训练集）
    apply_augmentation_flag = (split_name == 'train')

    # 获取增强倍数（仅训练集使用）
    augmentation_multiplier = 1
    if apply_augmentation_flag:
        augmentation_multiplier = config.get('augmentation_multiplier', 1)
        if augmentation_multiplier < 1:
            augmentation_multiplier = 1

    # 显示处理模式
    if apply_augmentation_flag:
        if augmentation_multiplier == 1:
            print(f"🔧 {split_name} 分割：应用预处理（不增强，仅保留原图）")
        else:
            augmentation_config = config.get('augmentation', [])
            if augmentation_config:
                print(f"✨ {split_name} 分割：应用预处理 + 数据增强 ({augmentation_multiplier}x)")
                print(f"   每张图片将生成: 1个原图 + {augmentation_multiplier - 1}个增强变体")
            else:
                print(f"🔧 {split_name} 分割：应用预处理（配置中无增强步骤）")
                augmentation_multiplier = 1  # 强制为1
    else:
        print(f"🔧 {split_name} 分割：仅应用预处理（不增强）")

    processed_count = 0
    error_count = 0
    validation_errors = 0
    total_variants = 0  # 总变体数（包括原图和所有增强变体）

    # 获取基础随机种子
    base_random_seed = config.get('random_seed', int(time.time()))

    for idx, (image_path, annotation_path, annotation_data) in enumerate(matched_pairs):
        try:
            original_filename = os.path.basename(image_path)
            base_name = os.path.splitext(original_filename)[0]
            file_ext = os.path.splitext(original_filename)[1]

            print(f"\n处理 [{idx+1}/{len(matched_pairs)}]: {original_filename} -> {split_name}/")

            # 提取标注
            annotations = annotation_data.get('annotations', [])

            # 加载原始图片
            with Image.open(image_path) as img:
                # 步骤1：预处理（所有分割都应用）
                preprocessed_img, preprocessed_annotations, pp_params = apply_preprocessing(
                    img, annotations, config.get('preprocessing', [])
                )

                # 步骤2：根据分割类型和增强倍数处理
                if apply_augmentation_flag and augmentation_multiplier > 1:
                    augmentation_config = config.get('augmentation', [])

                    # 2.1 保存原图（预处理后，未增强）
                    original_output_filename = f"{base_name}_original{file_ext}"
                    original_output_path = os.path.join(split_dir, original_output_filename)
                    preprocessed_img.save(original_output_path)
                    print(f"  💾 [原图] {original_output_filename}")

                    # 保存原图标注
                    if annotations:
                        original_ann_filename = f"{base_name}_original.json"
                        original_ann_path = os.path.join(split_dir, original_ann_filename)
                        original_ann_data = {
                            **annotation_data,
                            'annotations': preprocessed_annotations,
                            'processed_at': time.time(),
                            'split': split_name,
                            'variant_type': 'original',
                            'preprocessing_applied': bool(config.get('preprocessing')),
                            'augmentation_applied': False
                        }
                        # 更新data.image路径以匹配新的文件名
                        if 'data' in original_ann_data and 'image' in original_ann_data['data']:
                            original_ann_data['data']['image'] = original_output_filename
                        
                        with open(original_ann_path, 'w', encoding='utf-8') as f:
                            json.dump(original_ann_data, f, indent=2, ensure_ascii=False)

                    total_variants += 1

                    # 2.2 生成增强变体（N-1个）
                    for variant_idx in range(1, augmentation_multiplier):
                        # 使用不同的随机种子确保每个变体不同
                        variant_seed = base_random_seed + idx * 1000 + variant_idx
                        random.seed(variant_seed)

                        augmented_img, augmented_annotations, aug_params = apply_augmentation(
                            preprocessed_img, preprocessed_annotations, augmentation_config
                        )

                        # 保存增强变体图片
                        variant_filename = f"{base_name}_aug{variant_idx}{file_ext}"
                        variant_path = os.path.join(split_dir, variant_filename)
                        augmented_img.save(variant_path)
                        print(f"  ✨ [变体{variant_idx}] {variant_filename}")

                        # 保存增强变体标注
                        if annotations:
                            variant_ann_filename = f"{base_name}_aug{variant_idx}.json"
                            variant_ann_path = os.path.join(split_dir, variant_ann_filename)
                            variant_ann_data = {
                                **annotation_data,
                                'annotations': augmented_annotations,
                                'processed_at': time.time(),
                                'split': split_name,
                                'variant_type': f'augmented_{variant_idx}',
                                'variant_seed': variant_seed,
                                'preprocessing_applied': bool(config.get('preprocessing')),
                                'augmentation_applied': True,
                                'augmentation_params': aug_params
                            }
                            # 更新data.image路径以匹配新的文件名
                            if 'data' in variant_ann_data and 'image' in variant_ann_data['data']:
                                variant_ann_data['data']['image'] = variant_filename
                            
                            with open(variant_ann_path, 'w', encoding='utf-8') as f:
                                json.dump(variant_ann_data, f, indent=2, ensure_ascii=False)

                            # 验证增强变体标注
                            is_valid, issues = validate_annotation_coordinates(
                                variant_path,
                                variant_ann_data
                            )
                            if not is_valid:
                                validation_errors += 1
                                print(f"    ⚠️ 标注验证失败")
                                for issue in issues:
                                    print(f"      {issue}")
                                vis_path = os.path.join(validation_dir, variant_filename)
                                visualize_annotations(variant_path, variant_ann_data, vis_path)

                        total_variants += 1

                    processed_count += 1

                else:
                    # 非训练集 或 训练集但multiplier=1：只保存预处理后的原图
                    output_filename = original_filename
                    output_path = os.path.join(split_dir, output_filename)
                    preprocessed_img.save(output_path)
                    print(f"  💾 {output_filename}")

                    # 保存标注
                    if annotations:
                        output_ann_filename = f"{base_name}.json"
                        output_ann_path = os.path.join(split_dir, output_ann_filename)
                        output_ann_data = {
                            **annotation_data,
                            'annotations': preprocessed_annotations,
                            'processed_at': time.time(),
                            'split': split_name,
                            'preprocessing_applied': bool(config.get('preprocessing')),
                            'augmentation_applied': False
                        }
                        with open(output_ann_path, 'w', encoding='utf-8') as f:
                            json.dump(output_ann_data, f, indent=2, ensure_ascii=False)

                        # 验证标注
                        is_valid, issues = validate_annotation_coordinates(
                            output_path,
                            output_ann_data
                        )
                        if not is_valid:
                            validation_errors += 1
                            print(f"  ⚠️ 标注验证失败")
                            for issue in issues:
                                print(f"    {issue}")
                            vis_path = os.path.join(validation_dir, output_filename)
                            visualize_annotations(output_path, output_ann_data, vis_path)

                    total_variants += 1
                    processed_count += 1

        except Exception as e:
            print(f"❌ 处理 {os.path.basename(image_path)} 失败: {e}")
            error_count += 1
            import traceback
            traceback.print_exc()

    print(f"\n{split_name.upper()} 处理完成:")
    print(f"  原始样本: {len(matched_pairs)}")
    print(f"  成功处理: {processed_count}")
    print(f"  失败: {error_count}")
    print(f"  总变体数: {total_variants} (包括原图和增强变体)")
    if apply_augmentation_flag and augmentation_multiplier > 1:
        print(f"  增强倍数: {augmentation_multiplier}x")
    print(f"  标注验证错误: {validation_errors}")
    print(f"输出目录: {split_dir}")

    return processed_count, error_count, total_variants


def main():
    """
    版本快照处理主函数

    环境变量：
    - SNAPSHOT_PATH: 快照路径 (例如 /project_1/version_1)
    - CONFIG: JSON格式的配置字符串 (可选)

    重要：Pachyderm的glob模式 "/project_X/version_Y/*" 会将匹配的文件直接挂载到
    /pfs/version_snapshots/ 根目录，而不保留完整路径！
    实际挂载：
    - /pfs/version_snapshots/_metadata.json
    - /pfs/version_snapshots/annotations/
    - /pfs/version_snapshots/images/
    """
    start_time = time.time()
    output_dir = "/pfs/out"
    os.makedirs(output_dir, exist_ok=True)

    print("=== 版本快照处理 ===")
    print(f"输出目录: {output_dir}")

    # 从环境变量获取快照路径（仅用于日志和元数据）
    snapshot_path = os.environ.get('SNAPSHOT_PATH', '')
    if not snapshot_path:
        print("❌ 未设置SNAPSHOT_PATH环境变量")
        return

    print(f"快照路径（元数据）: {snapshot_path}")

    # Pachyderm的glob模式 "/project_X/version_Y/*" 会保留目录结构
    # 实际挂载：/pfs/version_snapshots/project_X/version_Y/
    # 所以需要拼接完整路径
    snapshot_base_dir = os.path.join("/pfs/version_snapshots", snapshot_path.lstrip('/'))

    print(f"实际数据目录: {snapshot_base_dir}")

    # 列出实际挂载的文件（用于调试）
    print(f"📂 检查挂载的文件:")
    pfs_root = "/pfs/version_snapshots"
    if os.path.exists(pfs_root):
        print(f"  /pfs/version_snapshots/ 内容:")
        for item in os.listdir(pfs_root):
            item_path = os.path.join(pfs_root, item)
            if os.path.isdir(item_path):
                print(f"    📁 {item}/")
                # 列出子目录
                for subitem in os.listdir(item_path):
                    subitem_path = os.path.join(item_path, subitem)
                    if os.path.isdir(subitem_path):
                        print(f"      📁 {subitem}/")
                    else:
                        print(f"      📄 {subitem}")
            else:
                print(f"    📄 {item}")
    else:
        print(f"⚠️ 目录不存在: {pfs_root}")

    if not os.path.exists(snapshot_base_dir):
        print(f"❌ 快照目录不存在: {snapshot_base_dir}")
        return

    # 加载元数据
    metadata = load_snapshot_metadata(snapshot_base_dir)

    # 获取配置（优先使用环境变量，其次使用元数据中的配置）
    config = {}
    config_str = os.environ.get('CONFIG', '')

    if config_str:
        try:
            config = json.loads(config_str)
            print("✅ 从环境变量加载配置")
        except Exception as e:
            print(f"⚠️ 环境变量CONFIG解析失败: {e}")

    if not config and 'config' in metadata:
        config = metadata['config']
        print("✅ 从元数据加载配置")

    if not config:
        print("⚠️ 未找到配置，使用默认配置")

    print(f"配置内容: {json.dumps(config, indent=2, ensure_ascii=False)}")

    # 扫描快照数据
    matched_pairs = find_snapshot_data(snapshot_base_dir)

    if not matched_pairs:
        print("❌ 没有找到有效的图片-标注对")
        return

    # 检查是否启用数据集划分
    split_config = config.get('split', {'enabled': False})

    if split_config.get('enabled', False):
        print(f"\n🎯 启用数据集划分模式")

        # 设置随机种子
        if 'random_seed' in config:
            random.seed(config['random_seed'])
            print(f"使用随机种子: {config['random_seed']}")

        # 执行数据集划分
        splits = split_dataset(matched_pairs, split_config)

        # 处理每个分割
        total_processed = 0
        total_errors = 0
        total_output_images = 0  # 总输出图片数（包括所有变体）
        split_results = {}

        for split_name, split_pairs in splits.items():
            if split_name != 'all':
                processed, errors, variants = process_split(split_name, split_pairs, config, output_dir)
                total_processed += processed
                total_errors += errors
                total_output_images += variants
                split_results[split_name] = {
                    'original_count': len(split_pairs),
                    'processed': processed,
                    'errors': errors,
                    'output_images': variants
                }

        # 创建处理报告
        augmentation_multiplier = config.get('augmentation_multiplier', 1)
        processing_report = {
            "version_id": metadata.get('version_id') or config.get('version_id'),
            "project_id": metadata.get('project_id') or config.get('project_id'),
            "snapshot_path": snapshot_path,
            "snapshot_mode": True,
            "dataset_split_enabled": True,
            "augmentation_multiplier": augmentation_multiplier,
            "split_config": split_config,
            "split_results": split_results,
            "total_matched_pairs": len(matched_pairs),
            "total_processed": total_processed,
            "total_errors": total_errors,
            "total_output_images": total_output_images,
            "preprocessing_steps": config.get('preprocessing', []),
            "augmentation_steps": config.get('augmentation', []),
            "processing_time": time.time() - start_time,
            "source_commits": metadata.get('source_commits', {}),
            "status": "completed" if total_errors == 0 else "completed_with_errors"
        }

    else:
        print(f"\n📁 使用传统处理模式（不划分数据集）")

        # 不划分，直接处理所有数据
        processed_count = 0
        error_count = 0

        for image_path, annotation_path, annotation_data in matched_pairs:
            try:
                print(f"\n--- 处理: {os.path.basename(image_path)} ---")

                annotations = annotation_data.get('annotations', [])

                with Image.open(image_path) as img:
                    # 应用预处理
                    processed_img, processed_annotations, pp_params = apply_preprocessing(
                        img, annotations, config.get('preprocessing', [])
                    )

                    # 应用增强
                    augmentation_config = config.get('augmentation', [])
                    if augmentation_config:
                        final_img, final_annotations, aug_params = apply_augmentation(
                            processed_img, processed_annotations, augmentation_config
                        )
                    else:
                        final_img = processed_img
                        final_annotations = processed_annotations

                    # 保存
                    original_filename = os.path.basename(image_path)
                    output_image_path = os.path.join(output_dir, original_filename)
                    final_img.save(output_image_path)

                    if annotations:
                        output_annotation_path = os.path.join(
                            output_dir,
                            os.path.splitext(original_filename)[0] + '.json'
                        )

                        output_annotation_data = {
                            **annotation_data,
                            'annotations': final_annotations,
                            'processed_at': time.time()
                        }

                        with open(output_annotation_path, 'w', encoding='utf-8') as f:
                            json.dump(output_annotation_data, f, indent=2, ensure_ascii=False)

                    processed_count += 1

            except Exception as e:
                print(f"❌ 处理失败: {e}")
                error_count += 1
                import traceback
                traceback.print_exc()

        # 创建传统模式报告
        processing_report = {
            "version_id": metadata.get('version_id') or config.get('version_id'),
            "project_id": metadata.get('project_id') or config.get('project_id'),
            "snapshot_path": snapshot_path,
            "snapshot_mode": True,
            "dataset_split_enabled": False,
            "total_matched_pairs": len(matched_pairs),
            "processed_pairs": processed_count,
            "failed_pairs": error_count,
            "preprocessing_steps": config.get('preprocessing', []),
            "augmentation_steps": config.get('augmentation', []),
            "processing_time": time.time() - start_time,
            "source_commits": metadata.get('source_commits', {}),
            "status": "completed" if error_count == 0 else "completed_with_errors"
        }

    # 保存处理报告
    report_path = os.path.join(output_dir, 'processing_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(processing_report, f, indent=2, ensure_ascii=False)
    print(f"\n📊 处理报告保存至: {report_path}")

    print(f"\n=== 版本快照处理完成 ===")
    if split_config.get('enabled', False):
        print(f"划分模式: 启用")
        augmentation_multiplier = config.get('augmentation_multiplier', 1)
        if augmentation_multiplier > 1:
            print(f"增强倍数: {augmentation_multiplier}x (仅训练集)")
        for split_name, result in processing_report.get('split_results', {}).items():
            orig_count = result['original_count']
            output_count = result['output_images']
            print(f"  {split_name.upper()}: {orig_count} 张原始 → {output_count} 张输出 ({result['processed']}/{orig_count} 成功)")
        print(f"总计: 原始 {len(matched_pairs)} 张, 输出 {total_output_images} 张")
        print(f"处理状态: 成功 {total_processed}, 失败 {total_errors}")
    else:
        print(f"传统模式: 成功 {processing_report.get('processed_pairs', 0)}, 失败 {processing_report.get('failed_pairs', 0)}")
    print(f"处理耗时: {time.time() - start_time:.2f}秒")
    print(f"输出目录: {output_dir}")


if __name__ == "__main__":
    main()
