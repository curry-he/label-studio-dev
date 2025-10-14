import logging
import json
from celery import shared_task
from .models import DatasetVersion
from . import pachyderm_utils as pachu

logger = logging.getLogger(__name__)

@shared_task
def process_version_creation(version_id):
    """
    创建数据集版本配置文件并提交到annotations仓库
    不再直接创建和运行管道，而是在导出时动态创建管道
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
        
        logger.info(f"📊 开始创建版本配置 {version_id}，项目 {project_id} (配置文件模式)")

        # 2. 仓库配置（仅用于配置文件）
        raw_images_repo = "raw_images"      # Source Storage repo name
        annotations_repo = "annotations"   # Target Storage repo name
        # 注意：不再预先创建输出仓库，而是在导出时动态创建
        
        logger.info(f"📋 版本配置:")
        logger.info(f"  - 原始图片仓库: {raw_images_repo}")
        logger.info(f"  - 标注仓库: {annotations_repo}")

        # 3. 准备处理配置（不包含管道执行信息）
        split_config = version.split_config or {"enabled": False}
        
        config_for_pfs = {
            "version_id": version_id,
            "project_id": project_id,
            "status": "config_created",  # 标记为配置已创建
            "created_mode": "deferred_processing",  # 延迟处理模式
            "smart_match_mode": True,
            "preprocessing": version.preprocessing_config or [],
            "augmentation": version.augmentation_config or [],
            "split": split_config,
            "created_at": timezone.now().isoformat(),
            "pipelines": {
                "raw_images_repo": raw_images_repo,
                "annotations_repo": annotations_repo,
                "ls_dataset_template": f"ls-dataset-{project_id}-v{version_id}",
                "format_converter_template": f"format-converter-yolo-{project_id}-v{version_id}"
            }
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

        # 4. 提交配置到annotations仓库（不启动管道）
        config_commit = pachu.commit_processing_config_to_repo(
            client, annotations_repo, config_for_pfs
        )
        logger.info(f"配置已提交到 {annotations_repo}, commit: {config_commit.id}")

        # 5. 更新版本状态为配置已创建
        version.pachyderm_input_commit = config_commit.id
        version.status = DatasetVersion.Status.CREATED  # 保持为CREATED状态
        version.processed_at = timezone.now()
        version.save()
        
        logger.info(f"🎉 版本 {version_id} 配置创建完成")
        logger.info(f"  - 配置文件: {annotations_repo}/_processing_config/config.json")
        logger.info(f"  - 配置提交ID: {config_commit.id}")
        logger.info(f"  - 状态: 等待导出时创建管道")

    except Exception as e:
        logger.error(f"版本 {version_id} 配置创建失败: {e}", exc_info=True)
        if version:
            version.status = DatasetVersion.Status.FAILED
            version.error_message = f"配置创建失败: {str(e)}"
            version.save()


@shared_task
def process_dataset_export(export_id, version_id, export_key=None):
    """
    处理数据集导出：动态创建管道、处理数据、生成下载链接
    导出完成后将结果复制到持久化仓库并清理临时管道
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
        
        # 3. 从配置文件中获取管道模板信息
        annotations_repo = "annotations"
        
        # 从annotations仓库中读取配置文件
        try:
            config_commit = pfs.Commit(repo=pfs.Repo(name=annotations_repo), id=version.pachyderm_input_commit)
            config_file_obj = pfs.File(commit=config_commit, path="/_processing_config/config.json")
            
            config_content = b''
            for chunk in client.pfs.get_file(file=config_file_obj):
                if hasattr(chunk, 'value'):
                    config_content += chunk.value
                elif isinstance(chunk, bytes):
                    config_content += chunk
                else:
                    config_content += bytes(chunk)
                    
            config_data = json.loads(config_content.decode('utf-8'))
            logger.info(f"📋 读取版本配置成功")
            
        except Exception as e:
            logger.error(f"读取版本配置失败: {e}")
            raise Exception(f"无法读取版本配置: {e}")
        
        # 4. 创建数据处理管道
        pipelines = config_data.get('pipelines', {})
        raw_images_repo = pipelines.get('raw_images_repo', 'raw_images')
        ls_dataset_repo = pipelines.get('ls_dataset_template', f"ls-dataset-{project_id}-v{version_id}")
        format_converter_repo = pipelines.get('format_converter_template', f"format-converter-yolo-{project_id}-v{version_id}")
        
        logger.info(f"🏗️ 开始创建处理管道:")
        logger.info(f"  - 数据处理管道: {ls_dataset_repo}")
        logger.info(f"  - 格式转换管道: {format_converter_repo}")
        
        # 创建智能匹配数据处理管道
        pipeline_spec = pachu.create_smart_match_pipeline_spec(
            raw_images_repo=raw_images_repo,
            annotations_repo=annotations_repo,
            output_repo=ls_dataset_repo,
            processing_config=config_data
        )
        
        pachu.create_or_update_pipeline(client, pipeline_spec)
        created_pipelines.append(ls_dataset_repo)
        logger.info(f"✅ 数据处理管道创建完成: {ls_dataset_repo}")
        
        # 5. 事件驱动等待数据处理管道完成
        logger.info(f"⏳ 开始事件驱动等待数据处理完成...")
        print("等待数据处理管道完成")
        success, dataset_output_commit = pachu.wait_for_pipeline_job_completion(
            client, ls_dataset_repo, timeout=60  # 1分钟超时
        )
        
        print(success)
        if not success:
            raise Exception(f"数据处理管道 {ls_dataset_repo} 执行失败")
        
        # 验证输出
        try:
            file_obj = pfs.File(commit=dataset_output_commit, path="/")
            files = list(client.pfs.list_file(file=file_obj))
            logger.info(f"📊 数据处理完成: {len(files)} 个文件/目录")
        except Exception as e:
            logger.error(f"验证数据处理输出失败: {e}")
            raise Exception(f"数据处理管道输出验证失败: {e}")
        
        # 6. 创建格式转换管道
        format_converter_spec = pachu.create_format_converter_pipeline_spec(
            input_repo=ls_dataset_repo,
            output_repo=format_converter_repo
        )
        
        pachu.create_or_update_pipeline(client, format_converter_spec)
        created_pipelines.append(format_converter_repo)
        logger.info(f"✅ 格式转换管道创建完成: {format_converter_repo}")
        
        # 7. 事件驱动等待格式转换管道完成
        logger.info(f"⏳ 开始事件驱动等待格式转换完成...")
        success, format_output_commit = pachu.wait_for_pipeline_job_completion(
            client, format_converter_repo, timeout=60  # 1分钟超时
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
                # 复制文件到持久化仓库（仓库已在检查阶段确保存在）
                logger.info(f"📂 开始复制文件到持久化仓库...")
                persistent_commit_id = pachu.copy_files_to_export_results(
                    client, format_output_commit, export_key, export_record.format
                )
                logger.info(f"✅ 导出结果已复制到持久化仓库，commit: {persistent_commit_id}")
            except Exception as e:
                logger.error(f"❌ 复制到持久化仓库失败: {e}")
                import traceback
                logger.error(f"详细错误信息: {traceback.format_exc()}")
                # 将 persistent_commit_id 明确设置为 None
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
        if persistent_commit_id:
            logger.info(f"  - 持久化仓库: export-results")
            logger.info(f"  - 持久化提交: {persistent_commit_id}")
        logger.info(f"  - 临时管道: {format_converter_repo}")
        logger.info(f"  - 临时提交: {format_output_commit.id}")
        logger.info(f"  - 数据处理时间: 事件驱动，精确响应")
        logger.info(f"  - 格式转换时间: 事件驱动，精确响应")
        
        # 11. 清理临时管道（始终进行，确保资源不浪费）
        logger.info("🧹 准备清理临时管道...")
        try:
            # 无论是否使用持久化存储，都应该清理临时管道
            # 延迟清理，确保第一次下载有机会完成
            from core.redis import start_job_async_or_sync
            
            # 如果使用了持久化存储，立即清理临时管道
            if persistent_commit_id:
                logger.info("✅ 使用持久化存储，立即启动临时管道清理")
                # start_job_async_or_sync(cleanup_export_pipelines, export_record.id)
            else:
                logger.info("⏰ 使用临时存储，延迟清理将在下载后进行")
                # 临时管道清理将在下载完成后触发
                
        except Exception as cleanup_error:
            logger.warning(f"启动管道清理任务失败: {cleanup_error}")
            # 不影响导出成功状态
        
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
            # cleanup_pipelines(client, created_pipelines)


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