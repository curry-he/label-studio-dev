import logging
import json
from celery import shared_task
from .models import DatasetVersion
from . import pachyderm_utils as pachu

logger = logging.getLogger(__name__)

@shared_task
def process_version_creation(version_id):
    """
    使用智能匹配模式的Pachyderm管道处理数据集版本
    通过标注数据中的原始文件名信息匹配图片和标注进行处理
    """
    from django.utils import timezone
    
    logger.info(f"🚀 Celery任务启动: process_version_creation(version_id={version_id})")
    
    version = None
    try:
        # 1. 初始设置
        version = DatasetVersion.objects.get(id=version_id)
        logger.info(f"📝 找到版本: {version.name} (ID: {version_id})")
        
        version.status = DatasetVersion.Status.PROCESSING
        version.save()
        logger.info(f"✅ 版本状态更新为 PROCESSING")

        logger.info(f"🔗 开始连接Pachyderm...")
        client = pachu.get_pachyderm_client()
        project_id = version.project.id
        
        logger.info(f"📊 开始处理版本 {version_id}，项目 {project_id} (智能匹配模式)")

        # 2. 使用智能匹配模式的仓库配置
        raw_images_repo = "raw_images"      # Source Storage repo name
        annotations_repo = "annotations"   # Target Storage repo name  
        output_repo = f"ls-dataset-{project_id}-v{version_id}"
        
        logger.info(f"📂 智能匹配输入仓库: {raw_images_repo} (原始图片) + {annotations_repo} (标注)")
        logger.info(f"📂 输出仓库: {output_repo}")

        # 3. 准备处理配置（包含前端传递的预处理、增强和分割配置）
        split_config = version.split_config or {"enabled": False}
        
        config_for_pfs = {
            "version_id": version_id,
            "project_id": project_id,
            "smart_match_mode": True,
            "preprocessing": version.preprocessing_config or [],
            "augmentation": version.augmentation_config or [],
            "split": split_config,
            "created_at": timezone.now().isoformat()
        }
        
        # 如果启用了数据集分割但没有指定随机种子，添加默认种子
        if split_config.get("enabled") and "random_seed" not in split_config:
            config_for_pfs["random_seed"] = version_id  # 使用version_id作为种子确保可重现性
        
        logger.info(f"📋 处理配置内容:")
        logger.info(f"  - 预处理: {len(version.preprocessing_config or [])} 个步骤")
        logger.info(f"  - 数据增强: {len(version.augmentation_config or [])} 个步骤")
        logger.info(f"  - 数据集分割: {'启用' if split_config.get('enabled') else '禁用'}")
        if "random_seed" in config_for_pfs:
            logger.info(f"  - 随机种子: {config_for_pfs['random_seed']}")
        if version.preprocessing_config:
            logger.info(f"  - 预处理详情: {version.preprocessing_config}")
        if version.augmentation_config:
            logger.info(f"  - 增强详情: {version.augmentation_config}")
        if split_config.get('enabled'):
            logger.info(f"  - 分割详情: {split_config}")

        # 4. 提交配置到annotations仓库（智能匹配脚本从这里读取config.json）
        config_commit = pachu.commit_processing_config_to_repo(
            client, annotations_repo, config_for_pfs
        )
        logger.info(f"配置已提交到 {annotations_repo}, commit: {config_commit.id}")

        # 5. 创建智能匹配管道规范
        pipeline_spec = pachu.create_smart_match_pipeline_spec(
            raw_images_repo=raw_images_repo,
            annotations_repo=annotations_repo, 
            output_repo=output_repo,
            processing_config=config_for_pfs
        )

        # 6. 创建或更新管道
        pachu.create_or_update_pipeline(client, pipeline_spec)
        logger.info(f"智能匹配管道已创建/更新: {output_repo}")

        # 7. 等待智能匹配管道完成并检查输出
        logger.info(f"等待智能匹配管道 {output_repo} 处理完成...")
        
        # 等待足够时间让管道处理（根据数据量调整）
        import time
        time.sleep(60)  # 等待3分钟
        
        # 使用更简单可靠的方法检查输出
        try:
            from pachyderm_sdk.api import pfs
            
            # 创建 master branch 对象
            master_branch = pfs.Branch(repo=pfs.Repo(name=output_repo), name="master")
            
            # 获取master分支的最新commit
            branch_info = client.pfs.inspect_branch(branch=master_branch)
            latest_commit = branch_info.head
            
            # 创建 File 对象用于 list_file 调用
            file_obj = pfs.File(commit=latest_commit, path="/")
            files = list(client.pfs.list_file(file=file_obj))
            
            if files:
                logger.info(f"智能匹配管道输出确认: {len(files)} 个文件/目录")
                # 使用已获取的commit
                output_commit = latest_commit
                logger.info(f"输出commit: {output_commit.id}")
            else:
                raise Exception(f"输出仓库 {output_repo} 没有文件")
                
        except Exception as e:
            logger.error(f"检查智能匹配输出失败: {e}")
            # 再等待一段时间重试
            logger.info("等待更长时间后重试...")
            time.sleep(60)  # 再等2分钟
            try:
                # 确保 master_branch 对象在重试作用域中
                master_branch = pfs.Branch(repo=pfs.Repo(name=output_repo), name="master")
                
                # 重新获取最新commit并使用正确的 File 对象调用list_file
                branch_info = client.pfs.inspect_branch(branch=master_branch)
                latest_commit = branch_info.head
                file_obj = pfs.File(commit=latest_commit, path="/")
                files = list(client.pfs.list_file(file=file_obj))
                if files:
                    logger.info(f"重试成功: {len(files)} 个文件/目录")
                    output_commit = latest_commit
                else:
                    raise Exception(f"重试后仍然没有数据: {output_repo}")
            except Exception as retry_e:
                logger.error(f"重试也失败: {retry_e}")
                raise

        # 8. 创建format-converter管道
        format_converter_repo = f"format-converter-yolo-{project_id}-v{version_id}"
        logger.info(f"📋 创建格式转换管道: {format_converter_repo}")
        
        format_converter_spec = pachu.create_format_converter_pipeline_spec(
            input_repo=output_repo,  # 使用智能匹配的输出作为输入
            output_repo=format_converter_repo
        )
        
        pachu.create_or_update_pipeline(client, format_converter_spec)
        logger.info(f"格式转换管道已创建/更新: {format_converter_repo}")

        # 9. 等待格式转换管道输出仓库有数据  
        logger.info(f"等待格式转换管道 {format_converter_repo} 处理完成...")
        time.sleep(120)  # 等待2分钟
        
        try:
            # 直接检查format-converter的master分支
            format_master_branch = pfs.Branch(repo=pfs.Repo(name=format_converter_repo), name="master")
            format_branch_info = client.pfs.inspect_branch(branch=format_master_branch)
            format_output_commit = format_branch_info.head
            
            # 使用正确的方式列出文件
            format_file_obj = pfs.File(commit=format_output_commit, path="/")
            format_files = list(client.pfs.list_file(file=format_file_obj))
            
            if format_files:
                logger.info(f"格式转换管道输出确认: {len(format_files)} 个文件/目录")
                logger.info(f"格式转换输出commit: {format_output_commit.id}")
            else:
                raise Exception(f"格式转换仓库 {format_converter_repo} 没有文件")
        except Exception as e:
            logger.error(f"检查格式转换输出失败: {e}")
            raise

        # 10. 存储结果
        version.pachyderm_input_commit = config_commit.id
        version.pachyderm_output_commit = format_output_commit.id  # 保存最终的格式转换输出
        version.processed_at = timezone.now()
        
        # 9. 获取处理结果报告
        try:
            from pachyderm_sdk.api import pfs
            report_commit = pfs.Commit(
                repo=pfs.Repo(name=output_repo),
                id=output_commit.id
            )
            
            try:
                # 尝试获取处理报告（智能匹配生成的报告文件名包含哈希）
                report_files = pachu.list_files_in_commit(client, report_commit)
                report_file = None
                for file_info in report_files:
                    if 'processing_report' in file_info['path'] and file_info['path'].endswith('.json'):
                        report_file = file_info['path']
                        break
                
                if report_file:
                    processing_report = pachu.get_result_from_commit(client, report_commit, report_file)
                    logger.info(f"获取处理报告: {processing_report}")
                    
                    # 验证智能匹配处理结果
                    if processing_report.get('intelligent_matching'):
                        # 检查数据集分割结果
                        if processing_report.get('dataset_split_enabled') and processing_report.get('split_results'):
                            split_results = processing_report['split_results']
                            total_matched = processing_report.get('total_matched_pairs', 0)
                            logger.info(f"智能匹配处理成功: {total_matched} 个图片-标注对")
                            logger.info(f"数据集分割结果:")
                            for split_name, result in split_results.items():
                                logger.info(f"  - {split_name.upper()}: {result['processed']}/{result['total']} 成功处理")
                        elif processing_report.get('matched_pairs', 0) > 0 or processing_report.get('processed_pairs', 0) > 0:
                            # 传统模式或旧格式的处理报告
                            matched_count = processing_report.get('matched_pairs', processing_report.get('processed_pairs', 0))
                            logger.info(f"智能匹配处理成功: {matched_count} 个图片-标注对")
                            logger.info("使用传统模式处理（未启用数据集分割）")
                        else:
                            logger.warning("智能匹配处理可能存在问题，请检查图片文件名和标注数据的匹配情况")
                    else:
                        logger.warning("智能匹配处理可能存在问题，请检查图片文件名和标注数据的匹配情况")
                else:
                    logger.warning("未找到处理报告文件")
                    
            except Exception as e:
                logger.warning(f"无法获取处理报告: {e}")
                processing_report = {"status": "completed", "intelligent_matching": True}
            
            # 获取处理后的文件列表
            result_files = pachu.list_files_in_commit(client, report_commit)
            logger.info(f"输出文件数量: {len(result_files)}")
            
        except Exception as e:
            logger.warning(f"获取处理结果时出错: {e}")

        # 10. 完成版本处理
        version.status = DatasetVersion.Status.COMPLETED
        version.save()
        
        logger.info(f"版本 {version_id} 处理完成 (智能匹配+格式转换模式)")
        logger.info(f"  - 数据处理: {raw_images_repo} + {annotations_repo} -> {output_repo}")
        logger.info(f"  - 格式转换: {output_repo} -> {format_converter_repo}")
        logger.info(f"  - 最终输出: {format_converter_repo} (commit: {format_output_commit.id})")
        logger.info(f"  - 数据集分割: {'启用' if split_config.get('enabled') else '禁用'}")

    except Exception as e:
        logger.error(f"版本 {version_id} 处理失败: {e}", exc_info=True)
        if version:
            version.status = DatasetVersion.Status.FAILED
            version.error_message = f"智能匹配处理失败: {str(e)}"
            version.save()