import logging
import json
from celery import shared_task
from .models import DatasetVersion
from . import pachyderm_utils as pachu

logger = logging.getLogger(__name__)

@shared_task
def process_version_creation(version_id):
    """
    创建版本快照：冻结当前数据状态，实现Roboflow风格的版本管理
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

        logger.info(f"📊 开始创建版本快照 {version_id}，项目 {project_id} (版本冻结模式)")

        # 2. 准备版本配置
        split_config = version.split_config or {"enabled": False}

        config = {
            "version_id": version_id,
            "project_id": project_id,
            "preprocessing": version.preprocessing_config or [],
            "augmentation": version.augmentation_config or [],
            "split": split_config,
            "created_at": timezone.now().isoformat(),
        }

        # 如果启用了数据集分割但没有指定随机种子，添加默认种子
        if split_config.get("enabled") and "random_seed" not in split_config:
            config["random_seed"] = version_id  # 使用version_id作为种子确保可重现性

        logger.info(f"📋 处理配置内容:")
        logger.info(f"  - 预处理: {len(version.preprocessing_config or [])} 个步骤")
        logger.info(f"  - 数据增强: {len(version.augmentation_config or [])} 个步骤")
        logger.info(f"  - 数据集分割: {'启用' if split_config.get('enabled') else '禁用'}")
        if "random_seed" in config:
            logger.info(f"  - 随机种子: {config['random_seed']}")

        # 3. 创建版本快照（核心功能：版本冻结）
        logger.info(f"📸 开始创建版本快照...")
        snapshot_path = pachu.create_version_snapshot(
            client, project_id, version_id, config
        )
        logger.info(f"✅ 版本快照创建完成: {snapshot_path}")

        # 4. 更新版本状态
        version.pachyderm_input_commit = snapshot_path  # 存储快照路径
        version.status = DatasetVersion.Status.CREATED  # 状态为已创建（冻结）
        version.processed_at = timezone.now()
        version.save()

        logger.info(f"🎉 版本 {version_id} 快照创建完成")
        logger.info(f"  - 快照路径: {snapshot_path}")
        logger.info(f"  - 状态: 版本已冻结，等待导出")

    except Exception as e:
        logger.error(f"版本 {version_id} 快照创建失败: {e}", exc_info=True)
        if version:
            version.status = DatasetVersion.Status.FAILED
            version.error_message = f"快照创建失败: {str(e)}"
            version.save()


@shared_task
def process_dataset_export(export_id, version_id, export_key=None):
    """
    处理数据集导出：使用版本快照动态创建管道、处理数据、生成下载链接
    采用版本快照机制，确保每次导出都基于冻结的数据版本
    """
    from django.utils import timezone
    from .models import DatasetExport, DatasetVersion
    from pachyderm_sdk.api import pfs, pps

    logger.info(f"🚀 开始处理导出任务: export_id={export_id}, version_id={version_id}")

    export_record = None
    version = None
    created_pipelines = []  # 记录创建的管道，用于后续清理

    try:
        # 1. 获取导出记录和版本信息
        export_record = DatasetExport.objects.get(id=export_id)
        version = DatasetVersion.objects.get(id=version_id)

        logger.info(f"📝 导出信息: format={export_record.format}, version={version.name}")

        # 2. 连接Pachyderm
        logger.info(f"🔗 开始连接Pachyderm...")
        client = pachu.get_pachyderm_client()
        project_id = version.project.id

        # 3. 从快照加载配置
        snapshot_path = version.pachyderm_input_commit  # 这是快照路径
        if not snapshot_path:
            raise Exception("版本快照不存在，无法进行导出")

        logger.info(f"📸 版本快照路径: {snapshot_path}")

        # 检查快照是否存在
        if not pachu.check_snapshot_exists(client, snapshot_path):
            raise Exception(f"快照不存在: {snapshot_path}")

        # 加载快照配置
        config_data = pachu.load_version_config(client, snapshot_path)
        logger.info(f"📋 从快照加载配置成功")

        # 4. 创建快照处理管道（临时）
        snapshot_processor_repo = f"snapshot-processor-{project_id}-v{version_id}"
        format_converter_repo = f"format-converter-yolo-{project_id}-v{version_id}"

        logger.info(f"🏗️ 开始创建快照处理管道:")
        logger.info(f"  - 快照处理管道: {snapshot_processor_repo}")
        logger.info(f"  - 格式转换管道: {format_converter_repo}")

        # 创建快照处理管道
        snapshot_pipeline_spec = pachu.create_snapshot_processing_pipeline(
            pipeline_name=snapshot_processor_repo,
            snapshot_path=snapshot_path,
            config=config_data
        )

        pachu.create_or_update_pipeline(client, snapshot_pipeline_spec)
        created_pipelines.append(snapshot_processor_repo)
        logger.info(f"✅ 快照处理管道创建完成: {snapshot_processor_repo}")

        # 5. 事件驱动等待快照处理管道完成
        logger.info(f"⏳ 开始事件驱动等待快照处理完成...")
        success, snapshot_output_commit = pachu.wait_for_pipeline_job_completion(
            client, snapshot_processor_repo, timeout=600  # 10分钟超时
        )

        if not success:
            raise Exception(f"快照处理管道 {snapshot_processor_repo} 执行失败")

        # 验证输出
        try:
            file_obj = pfs.File(commit=snapshot_output_commit, path="/")
            files = list(client.pfs.list_file(file=file_obj))
            logger.info(f"📊 快照处理完成: {len(files)} 个文件/目录")
        except Exception as e:
            logger.error(f"验证快照处理输出失败: {e}")
            raise Exception(f"快照处理管道输出验证失败: {e}")

        # 6. 创建格式转换管道
        format_converter_spec = pachu.create_format_converter_pipeline_spec(
            input_repo=snapshot_processor_repo,
            output_repo=format_converter_repo
        )

        pachu.create_or_update_pipeline(client, format_converter_spec)
        created_pipelines.append(format_converter_repo)
        logger.info(f"✅ 格式转换管道创建完成: {format_converter_repo}")

        # 7. 事件驱动等待格式转换管道完成
        logger.info(f"⏳ 开始事件驱动等待格式转换完成...")
        success, format_output_commit = pachu.wait_for_pipeline_job_completion(
            client, format_converter_repo, timeout=600  # 10分钟超时
        )

        if not success:
            raise Exception(f"格式转换管道 {format_converter_repo} 执行失败")

        # 验证格式转换输出
        try:
            format_file_obj = pfs.File(commit=format_output_commit, path="/")
            format_files = list(client.pfs.list_file(file=format_file_obj))
            logger.info(f"📦 格式转换完成: {len(format_files)} 个文件/目录")
        except Exception as e:
            logger.error(f"验证格式转换输出失败: {e}")
            raise Exception(f"格式转换管道输出验证失败: {e}")

        # 8. 复制结果到持久化仓库（如果提供了export_key）
        persistent_commit_id = None
        if export_key:
            try:
                logger.info(f"🗄️ 开始复制导出结果到持久化仓库: {export_key}")
                # 确保持久化仓库存在
                pachu.ensure_export_results_repo(client)
                # 复制文件到持久化仓库
                persistent_commit_id = pachu.copy_files_to_export_results(
                    client, format_output_commit, export_key, export_record.format
                )
                logger.info(f"✅ 导出结果已复制到持久化仓库，commit: {persistent_commit_id}")
            except Exception as e:
                logger.error(f"❌ 复制到持久化仓库失败: {e}")
                import traceback
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                persistent_commit_id = None

        # 9. 生成下载链接
        if persistent_commit_id and export_key:
            # 优先使用持久化仓库的下载链接
            download_url = f"/api/projects/{project_id}/dataset-versions/{version_id}/download-persistent/{export_key}/"
            logger.info(f"🔗 使用持久化仓库下载链接: {download_url}")
        else:
            # 降级到临时管道下载链接
            download_url = f"/api/projects/{project_id}/dataset-versions/{version_id}/download-export/{format_converter_repo}/{format_output_commit.id}/"
            logger.info(f"🔗 使用临时管道下载链接: {download_url}")

        # 10. 更新导出记录
        if persistent_commit_id and export_key:
            # 使用持久化仓库信息
            export_record.pachyderm_pipeline_name = "export-results"
            export_record.pachyderm_output_commit = persistent_commit_id
        else:
            # 使用临时管道信息
            export_record.pachyderm_pipeline_name = format_converter_repo
            export_record.pachyderm_output_commit = format_output_commit.id

        export_record.download_url = download_url
        export_record.status = DatasetExport.ExportStatus.COMPLETED
        export_record.completed_at = timezone.now()
        export_record.progress = 100.0
        export_record.save()

        logger.info(f"🎉 导出任务完成!")
        logger.info(f"  - 导出格式: {export_record.format}")
        logger.info(f"  - 下载链接: {download_url}")
        logger.info(f"  - 快照路径: {snapshot_path}")
        if persistent_commit_id:
            logger.info(f"  - 持久化仓库: export-results")
            logger.info(f"  - 持久化提交: {persistent_commit_id}")

        # 11. 清理临时管道
        logger.info("🧹 准备清理临时管道...")
        try:
            from core.redis import start_job_async_or_sync

            # 如果使用了持久化存储，立即清理临时管道
            if persistent_commit_id:
                logger.info("✅ 使用持久化存储，立即启动临时管道清理")
                # 可以在这里启动清理任务
            else:
                logger.info("⏰ 使用临时存储，延迟清理将在下载后进行")

        except Exception as cleanup_error:
            logger.warning(f"启动管道清理任务失败: {cleanup_error}")

    except Exception as e:
        logger.error(f"导出任务失败: {e}", exc_info=True)

        if export_record:
            export_record.status = DatasetExport.ExportStatus.FAILED
            export_record.error_message = f"导出失败: {str(e)}"
            export_record.completed_at = timezone.now()
            export_record.save()

        # 清理已创建的管道
        if created_pipelines and client:
            logger.info("🧹 清理已创建的管道...")
            cleanup_pipelines(client, created_pipelines)


def cleanup_pipelines(client, pipeline_names):
    """清理指定的管道"""
    for pipeline_name in pipeline_names:
        pachu.cleanup_pipeline_safely(client, pipeline_name)
            

@shared_task
def cleanup_export_pipelines(export_id):
    """
    导出下载完成后清理相关管道，但保留持久化仓库
    """
    import time
    
    # 等待10秒，确保下载完成
    logger.info(f"🧹 等待10秒后开始清理导出 {export_id} 的相关管道...")
    time.sleep(10)
    
    logger.info(f"🧹 开始清理导出 {export_id} 的相关管道")
    
    try:
        from .models import DatasetExport
        export_record = DatasetExport.objects.get(id=export_id)
        
        if not export_record.pachyderm_pipeline_name:
            logger.info(f"导出 {export_id} 没有关联的管道，无需清理")
            return
        
        client = pachu.get_pachyderm_client()
        
        # 如果是持久化仓库，需要清理对应的临时管道
        if export_record.pachyderm_pipeline_name == "export-results":
            logger.info(f"导出 {export_id} 使用持久化仓库，清理对应的临时管道")
            
            # 根据导出记录重新构建临时管道名称
            version = export_record.dataset_version
            project_id = version.project.id
            version_id = version.id
            
            # 构建临时管道名称（与process_dataset_export中的命名保持一致）
            ls_dataset_repo = f"ls-dataset-{project_id}-v{version_id}"
            format_converter_repo = f"format-converter-yolo-{project_id}-v{version_id}"
            
            logger.info(f"清理临时管道: {ls_dataset_repo}, {format_converter_repo}")
            cleanup_pipelines(client, [ls_dataset_repo, format_converter_repo])
            
        else:
            # 常规清理逻辑（非持久化仓库）
            pipeline_name = export_record.pachyderm_pipeline_name
            
            # 推断数据处理管道名称（格式转换管道的前级）
            if pipeline_name.startswith("format-converter-yolo-"):
                dataset_pipeline_name = pipeline_name.replace("format-converter-yolo-", "ls-dataset-")
                cleanup_pipelines(client, [dataset_pipeline_name, pipeline_name])
            else:
                cleanup_pipelines(client, [pipeline_name])
        
        logger.info(f"✅ 导出 {export_id} 的临时管道清理完成")
        
    except Exception as e:
        logger.error(f"清理导出 {export_id} 的管道失败: {e}", exc_info=True)