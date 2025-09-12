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

        # 3. 准备处理配置（包含前端传递的预处理和增强配置）
        config_for_pfs = {
            "version_id": version_id,
            "project_id": project_id,
            "smart_match_mode": True,
            "preprocessing": version.preprocessing_config or [],
            "augmentation": version.augmentation_config or [],
            "created_at": timezone.now().isoformat()
        }
        
        logger.info(f"📋 处理配置内容:")
        logger.info(f"  - 预处理: {len(version.preprocessing_config or [])} 个步骤")
        logger.info(f"  - 数据增强: {len(version.augmentation_config or [])} 个步骤")
        if version.preprocessing_config:
            logger.info(f"  - 预处理详情: {version.preprocessing_config}")
        if version.augmentation_config:
            logger.info(f"  - 增强详情: {version.augmentation_config}")

        # 4. 提交配置到raw_images仓库（智能匹配脚本从这里读取config.json）
        config_commit = pachu.commit_processing_config_to_repo(
            client, raw_images_repo, config_for_pfs
        )
        logger.info(f"配置已提交到 {raw_images_repo}, commit: {config_commit.id}")

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

        # 7. 等待智能匹配管道处理完成
        output_commit = pachu.wait_for_job_completion(client, config_commit, output_repo)
        logger.info(f"智能匹配管道处理完成, output commit: {output_commit.id}")

        # 8. 存储结果
        version.pachyderm_input_commit = config_commit.id
        version.pachyderm_output_commit = output_commit.id
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
                    if processing_report.get('intelligent_matching') and processing_report.get('matched_pairs', 0) > 0:
                        logger.info(f"智能匹配处理成功: {processing_report['matched_pairs']} 个图片-标注对")
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
        
        logger.info(f"版本 {version_id} 处理完成 (智能匹配模式)")
        logger.info(f"  - 输入: {raw_images_repo} + {annotations_repo} (智能匹配)")
        logger.info(f"  - 输出: {output_repo} (commit: {output_commit.id})")

    except Exception as e:
        logger.error(f"版本 {version_id} 处理失败: {e}", exc_info=True)
        if version:
            version.status = DatasetVersion.Status.FAILED
            version.error_message = f"智能匹配处理失败: {str(e)}"
            version.save()