import logging
import time
import json
import os
import requests

import pachyderm_sdk
from pachyderm_sdk.api import pfs, pps

logger = logging.getLogger(__name__)

def get_pachyderm_client():
    """Establishes a connection to the Pachyderm cluster."""
    try:
        # Connects from a Docker container to a service on the host
        client = pachyderm_sdk.Client(
            host='localhost',
            port=80,
            auth_token='3bfd1a78e77641eabbae2df94faa5b3c',
            root_certs=None,
            transaction_id=None,
            tls=False
        )
        # A quick check to ensure the connection is valid
        client.get_version()
        logger.info("Successfully connected to Pachyderm.")
        return client
    except Exception as e:
        logger.error(f"Failed to connect to Pachyderm: {e}")
        raise

def ensure_repos_exist(client: pachyderm_sdk.Client, input_repo: str, output_repo: str):
    """Creates Pachyderm repositories if they don't already exist."""
    client.pfs.create_repo(repo=pfs.Repo(name=input_repo, type="user"), update=True)
    client.pfs.create_repo(repo=pfs.Repo(name=output_repo, type="user"), update=True)
    logger.info(f"Repos '{input_repo}' and '{output_repo}' are ready.")

def commit_data_from_project(client: pachyderm_sdk.Client, repo_name: str, project, config_for_pfs: dict) -> pfs.Commit:
    """Exports annotated tasks from a Label Studio project and commits them to a Pachyderm repo.
    
    This function should commit the annotated/labeled data (not raw data) to be processed into dataset versions.
    The logic follows Label Studio's Cloud Storage model:
    - Source Storage: Raw/original data
    - Target Storage: Annotated/labeled data (this is what we process)
    
    注意：不再在此函数中提交config.json，配置文件现在单独提交到annotations仓库
    """
    
    with client.pfs.commit(branch=pfs.Branch.from_uri(f"{repo_name}@master")) as commit:
        # 只提交数据文件，不提交配置文件
        logger.info(f"开始提交数据到仓库 '{repo_name}'（不包含配置文件）")
        
        # Get tasks that have been completed (annotated)
        annotated_tasks = project.tasks.filter(annotations__isnull=False).distinct()
        logger.info(f"Found {annotated_tasks.count()} annotated tasks to process")
        
        # Create data directory for annotated images and their labels
        for i, task in enumerate(annotated_tasks):
            if 'image' in task.data:
                image_url = task.data['image']
                try:
                    # Extract filename from URL or use task ID as fallback
                    if image_url.startswith('http'):
                        filename = image_url.split('/')[-1]
                        if not filename or '.' not in filename:
                            filename = f"image_{task.id}.jpg"
                        
                        # Download image from URL
                        response = requests.get(image_url, stream=True)
                        response.raise_for_status()
                        commit.put_file_from_fileobj(path=f"/data/{filename}", fileo=response.raw)
                        logger.info(f"Downloaded and committed image: {filename}")
                        
                        # Export and commit annotations for this task
                        annotations_data = []
                        for annotation in task.annotations.all():
                            if annotation.result:
                                annotations_data.append({
                                    'id': annotation.id,
                                    'result': annotation.result,
                                    'created_at': annotation.created_at.isoformat() if annotation.created_at else None,
                                    'updated_at': annotation.updated_at.isoformat() if annotation.updated_at else None,
                                    'task_id': task.id
                                })
                        
                        if annotations_data:
                            # Save annotations with same base filename as image
                            annotation_filename = f"{filename.rsplit('.', 1)[0]}.json"
                            commit.put_file_from_bytes(
                                path=f"/data/{annotation_filename}",
                                data=json.dumps(annotations_data, indent=2).encode('utf-8')
                            )
                            logger.info(f"Committed annotations: {annotation_filename}")
                        
                    else:
                        # Handle local file paths or cloud storage URLs
                        logger.warning(f"Local/cloud path detected: {image_url}. Attempting to access...")
                        # For cloud storage or local paths, you might need to implement specific logic
                        # depending on your Label Studio configuration
                        
                except Exception as e:
                    logger.error(f"Failed to process task {task.id} with image {image_url}: {e}")
                    continue
        
        # If no annotated tasks found, we might want to process all tasks
        if annotated_tasks.count() == 0:
            logger.warning("No annotated tasks found. Processing all tasks as fallback...")
            all_tasks = project.tasks.all()
            for i, task in enumerate(all_tasks[:10]):  # Limit to first 10 for safety
                if 'image' in task.data:
                    image_url = task.data['image']
                    try:
                        if image_url.startswith('http'):
                            filename = f"image_{task.id}_{image_url.split('/')[-1]}"
                            response = requests.get(image_url, stream=True)
                            response.raise_for_status()
                            commit.put_file_from_fileobj(path=f"/data/{filename}", fileo=response.raw)
                            logger.info(f"Committed raw task image: {filename}")
                    except Exception as e:
                        logger.error(f"Failed to download raw image {image_url}: {e}")
                        continue
        
        logger.info(f"Data for project {project.id} committed to repo '{repo_name}'. Commit ID: {commit.id}")
        return commit

def create_join_pipeline_spec(raw_images_repo: str, annotations_repo: str, output_repo: str, processing_config: dict) -> dict:
    """
    根据dev_plan.txt创建JOIN输入的管道规范
    实现原始图片仓库和标注仓库的JOIN逻辑，只处理已标注的数据
    
    Args:
        raw_images_repo: 原始图片仓库名 (Source Cloud Storage)
        annotations_repo: 标注仓库名 (Target Cloud Storage) 
        output_repo: 输出仓库名
        processing_config: 处理配置
    
    Returns:
        Pachyderm管道规范字典
    """
    pipeline_spec = {
        "pipeline": {"name": output_repo},
        "description": f"JOIN模式数据处理管道 - 只处理已标注数据",
        "input": {
            "join": [
                {
                    "pfs": {
                        "repo": raw_images_repo,
                        "glob": "/(*)",  # 捕获文件名作为JOIN键
                        "join_on": "$1"  # 使用第一个捕获组作为JOIN键
                    }
                },
                {
                    "pfs": {
                        "repo": annotations_repo,
                        "glob": "/(*).json",  # 匹配.json文件，捕获基础文件名
                        "join_on": "$1"  # 使用第一个捕获组作为JOIN键
                    }
                }
            ]
        },
        "transform": {
            "image": "localhost:5000/ls-processor:latest",
            "cmd": ["python", "/app/process_data.py"]
        }
    }
    
    logger.info(f"创建JOIN管道规范: {raw_images_repo} JOIN {annotations_repo} -> {output_repo}")
    return pipeline_spec

def commit_processing_config_to_repo(client: pachyderm_sdk.Client, repo_name: str, config: dict) -> pfs.Commit:
    """
    提交处理配置到指定的仓库，用于触发JOIN管道
    
    Args:
        client: Pachyderm客户端
        repo_name: 目标仓库名（通常是annotations仓库）
        config: 处理配置字典
        
    Returns:
        提交对象
    """
    with client.pfs.commit(branch=pfs.Branch.from_uri(f"{repo_name}@master")) as commit:
        # 将配置文件放在特殊目录中，避免被识别为源数据
        commit.put_file_from_bytes(
            path="/_processing_config/config.json", 
            data=json.dumps(config, indent=2, ensure_ascii=False).encode('utf-8')
        )
        logger.info(f"提交处理配置到仓库 '{repo_name}' 的 _processing_config/ 目录, commit: {commit.id}")
        return commit

def create_smart_match_pipeline_spec(raw_images_repo: str, annotations_repo: str, output_repo: str, processing_config: dict) -> dict:
    """
    创建智能匹配模式的管道规范
    通过标注数据中的原始文件名信息匹配图片和标注进行处理
    
    注意：配置文件现在位于annotations仓库的/_processing_config/config.json
    处理脚本应该从annotations仓库读取配置，并过滤掉_processing_config目录
    
    Args:
        raw_images_repo: 原始图片仓库名 (Source Cloud Storage)
        annotations_repo: 标注仓库名 (Target Cloud Storage) 
        output_repo: 输出仓库名
        processing_config: 处理配置
    
    Returns:
        Pachyderm管道规范字典
    """
    pipeline_spec = {
        "pipeline": {
            "name": output_repo,
            "project": {
                "name": "default"
            }
        },
        "description": "智能匹配模式：通过标注数据中的原始文件名信息匹配图片和标注，配置文件位于annotations仓库的_processing_config目录",
        "input": {
            "cross": [
                {
                    "pfs": {
                        "repo": raw_images_repo,
                        "glob": "/"
                    }
                },
                {
                    "pfs": {
                        "repo": annotations_repo,
                        "glob": "/"
                    }
                }
            ]
        },
        "transform": {
            "image": "localhost:5000/ls-processor:latest",
            "cmd": [
                "python",
                "/app/process_data_smart_match.py"
            ]
        }
    }
    
    logger.info(f"创建智能匹配管道规范: {raw_images_repo} + {annotations_repo} -> {output_repo}")
    logger.info(f"配置文件路径: {annotations_repo}/_processing_config/config.json")
    return pipeline_spec

def create_format_converter_pipeline_spec(input_repo: str, output_repo: str, export_format: str = "YOLO", include_original: bool = True, include_augmented: bool = True) -> dict:
    """
    创建格式转换管道规范
    将数据集处理输出转换为指定格式
    
    Args:
        input_repo: 输入仓库名（来自ls-dataset管道的输出）
        output_repo: 输出仓库名
        export_format: 导出格式 (YOLO, COCO, VOC等) - 暂时未使用，使用默认行为
        include_original: 是否包含原始数据 (暂时未使用)
        include_augmented: 是否包含增强数据 (暂时未使用)
    
    Returns:
        Pachyderm管道规范字典
    """
    # 使用最简单的命令，只传递必需的参数
    cmd = [
        "python",
        "/app/converter.py",
        "--input_dir", "/pfs/" + input_repo,
        "--output_dir", "/pfs/out"
    ]
    
    pipeline_spec = {
        "pipeline": {
            "name": output_repo,
            "project": {
                "name": "default"
            }
        },
        "description": f"格式转换管道：将 {input_repo} 的输出转换为格式化数据集",
        "input": {
            "pfs": {
                "repo": input_repo,
                "glob": "/"
            }
        },
        "transform": {
            "image": "localhost:5000/format-converter:latest",
            "cmd": cmd
        }
    }
    
    logger.info(f"创建格式转换管道规范: {input_repo} -> {output_repo}")
    return pipeline_spec

def create_or_update_pipeline(client: pachyderm_sdk.Client, spec: dict):
    """Creates or updates a Pachyderm pipeline from a dictionary spec."""
    client.pps.create_pipeline_v2(create_pipeline_request_json=json.dumps(spec), update=True)
    pipeline_name = spec.get("pipeline", {}).get("name", "unknown")
    logger.info(f"Pipeline '{pipeline_name}' created/updated successfully.")

def wait_for_pipeline_output(client: pachyderm_sdk.Client, output_repo_name: str, timeout: int = 600) -> pfs.Commit:
    """
    等待管道输出仓库有数据生成
    更可靠的方法：直接检查输出仓库是否有文件
    """
    logger.info(f"等待管道输出仓库 {output_repo_name} 生成数据...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            # 检查仓库的最新commit
            repo_info = client.pfs.inspect_repo(repo=pfs.Repo(name=output_repo_name))
            if repo_info.branches:
                # 获取master分支的最新commit
                master_commit = None
                for branch in repo_info.branches:
                    if branch.branch.name == "master":
                        master_commit = branch.head
                        break
                
                if master_commit:
                    # 检查commit是否有文件
                    try:
                        # 使用正确的 File 对象调用 list_file
                        file_obj = pfs.File(commit=master_commit, path="/")
                        files = list(client.pfs.list_file(file=file_obj))
                        if files:
                            logger.info(f"管道输出完成! 仓库 {output_repo_name} 包含 {len(files)} 个文件/目录")
                            logger.info(f"输出commit: {master_commit.id}")
                            return master_commit
                    except Exception as e:
                        logger.debug(f"检查文件列表时出错: {e}")
            
        except Exception as e:
            logger.debug(f"检查仓库 {output_repo_name} 时出错: {e}")
        
        logger.info(f"等待中... ({int(time.time() - start_time)}s/{timeout}s)")
        time.sleep(10)
    
    raise Exception(f"管道输出超时 {timeout} 秒，仓库 {output_repo_name} 仍然没有数据")

def wait_for_job_completion(client: pachyderm_sdk.Client, input_commit: pfs.Commit, output_repo_name: str, timeout: int = 600) -> pfs.Commit:
    """Polls Pachyderm until the job triggered by the input commit is finished."""
    logger.info(f"Waiting for job to finish for input commit {input_commit.id}...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        inspect_result = client.pfs.inspect_commit_set(commit_set=pfs.CommitSet(id=input_commit.id))
        for commit_info in inspect_result:
            if commit_info.commit.repo.name == output_repo_name and commit_info.finished:
                logger.info(f"Job finished! Output commit: {commit_info.commit.id}")
                return commit_info.commit
        
        time.sleep(10)
    
    raise Exception(f"Pipeline job timed out after {timeout} seconds for input commit {input_commit.id}")

def get_result_from_commit(client: pachyderm_sdk.Client, output_commit: pfs.Commit, file_path: str = "/output.json") -> dict:
    """Reads and parses a JSON result file from a given commit."""
    logger.info(f"Reading result file '{file_path}' from commit {output_commit.id}")
    # 使用正确的 File 对象调用 get_file
    file_obj = pfs.File(commit=output_commit, path=file_path)
    result_file = client.pfs.get_file(file=file_obj)
    
    # 检查返回类型并正确处理
    content_bytes = b''
    if hasattr(result_file, 'read'):
        # 如果是文件类对象
        content_bytes = result_file.read()
    else:
        # 如果是生成器，遍历所有数据块
        for chunk in result_file:
            if hasattr(chunk, 'value'):
                # 如果是 BytesValue 对象，提取 value 属性
                content_bytes += chunk.value
            elif isinstance(chunk, bytes):
                # 如果是普通字节
                content_bytes += chunk
            else:
                # 尝试转换为字节
                content_bytes += bytes(chunk)
    
    result_content = content_bytes.decode('utf-8')
    logger.info(f"Result content: {result_content}")
    return json.loads(result_content)

def list_files_in_commit(client: pachyderm_sdk.Client, commit: pfs.Commit) -> list:
    """Lists all files in a given commit."""
    try:
        file_list = []
        # 使用正确的 File 对象调用 list_file
        file_obj = pfs.File(commit=commit, path="/")
        for file_info in client.pfs.list_file(file=file_obj):
            file_list.append({
                'path': file_info.file.path,
                'size': file_info.size_bytes,
                'committed': file_info.file.commit.id
            })
        logger.info(f"Found {len(file_list)} files in commit {commit.id}")
        return file_list
    except Exception as e:
        logger.error(f"Error listing files in commit {commit.id}: {e}")
        return []

def get_processed_files_from_version(client: pachyderm_sdk.Client, version_commit: pfs.Commit, file_pattern: str = "*.jpg,*.png,*.json") -> list:
    """Gets processed files matching the pattern from a version commit."""
    try:
        processed_files = []
        # 使用正确的 File 对象调用 list_file
        file_obj = pfs.File(commit=version_commit, path="/")
        for file_info in client.pfs.list_file(file=file_obj):
            file_path = file_info.file.path
            # 检查文件扩展名
            if any(file_path.lower().endswith(ext.strip('*')) for ext in file_pattern.split(',')):
                processed_files.append({
                    'path': file_path,
                    'size': file_info.size_bytes,
                    'commit': file_info.file.commit.id,
                    'type': 'image' if file_path.lower().endswith(('.jpg', '.png', '.jpeg')) else 'annotation'
                })
        
        logger.info(f"Found {len(processed_files)} processed files matching pattern {file_pattern}")
        return processed_files
    except Exception as e:
        logger.error(f"Error getting processed files: {e}")
        return []


def delete_pipeline(client: pachyderm_sdk.Client, pipeline_name: str, force: bool = True):
    """删除指定的Pachyderm管道
    
    Args:
        client: Pachyderm客户端
        pipeline_name: 管道名称
        force: 是否强制删除，即使有错误也删除
    """
    from pachyderm_sdk.api import pps
    
    try:
        pipeline = pps.Pipeline(name=pipeline_name)
        client.pps.delete_pipeline(pipeline=pipeline, force=force)
        logger.info(f"成功删除管道: {pipeline_name}")
        return True
    except Exception as e:
        logger.error(f"删除管道 {pipeline_name} 失败: {e}")
        return False


def pipeline_exists(client: pachyderm_sdk.Client, pipeline_name: str) -> bool:
    """检查管道是否存在
    
    Args:
        client: Pachyderm客户端  
        pipeline_name: 管道名称
        
    Returns:
        bool: 管道是否存在
    """
    from pachyderm_sdk.api import pps
    
    try:
        pipeline = pps.Pipeline(name=pipeline_name)
        client.pps.inspect_pipeline(pipeline=pipeline)
        return True
    except Exception:
        return False


def cleanup_pipeline_safely(client: pachyderm_sdk.Client, pipeline_name: str):
    """安全地清理管道，包含存在性检查
    
    Args:
        client: Pachyderm客户端
        pipeline_name: 管道名称
    """
    if pipeline_exists(client, pipeline_name):
        logger.info(f"管道 {pipeline_name} 存在，开始删除...")
        success = delete_pipeline(client, pipeline_name, force=True)
        if success:
            logger.info(f"管道 {pipeline_name} 删除成功")
        else:
            logger.warning(f"管道 {pipeline_name} 删除失败")
    else:
        logger.info(f"管道 {pipeline_name} 不存在，无需删除")


def get_state_name(state):
    """安全地获取状态名称的通用函数"""
    if hasattr(state, 'name'):
        return state.name
    elif isinstance(state, int):
        # 如果是整数，根据JobState枚举映射
        state_mapping = {
            0: 'JOB_STATE_UNKNOWN',
            1: 'JOB_CREATED', 
            2: 'JOB_STARTING',
            3: 'JOB_RUNNING',
            4: 'JOB_FAILURE', 
            5: 'JOB_SUCCESS',
            6: 'JOB_KILLED',
            7: 'JOB_EGRESSING',
            8: 'JOB_FINISHING',
            9: 'JOB_UNRUNNABLE'
        }
        return state_mapping.get(state, f'UNKNOWN_STATE_{state}')
    else:
        return str(state)


def monitor_pipeline_jobs(client: pachyderm_sdk.Client, pipeline_name: str, callback=None, timeout: int = 600):
    """
    通过轮询监听管道Job状态变化的健壮版本

    Args:
        client: Pachyderm客户端
        pipeline_name: 要监听的管道名称
        callback: 回调函数，参数为(job_info, job_state)
        timeout: 监听超时时间（秒）
        
    Returns:
        (final_state, job_info): 最终的Job状态和Job信息，超时则job_info为None
    """
    from pachyderm_sdk.api import pps
    
    logger.info(f"🔍 开始监听管道 {pipeline_name} 的Job状态 (轮询模式)...")
    
    pipeline = pps.Pipeline(name=pipeline_name)
    start_time = time.time()
    
    last_job_id = None
    last_state_name = None

    while time.time() - start_time < timeout:
        try:
            # 获取该管道最新的一个Job
            jobs = list(client.pps.list_job(pipeline=pipeline, details=True, number=1))
            
            if not jobs:
                logger.info(f"📊 管道 {pipeline_name} 暂时没有Job，等待Job创建...")
                time.sleep(3)
                continue

            latest_job = jobs[0]
            job_id = latest_job.job.id
            state_name = get_state_name(latest_job.state)

            # 仅在状态或Job ID变化时打印日志，避免日志刷屏
            if job_id != last_job_id or state_name != last_state_name:
                logger.info(f"📊 管道 {pipeline_name} 最新Job状态: {state_name} (Job ID: {job_id})")
                last_job_id = job_id
                last_state_name = state_name
            
            # 调用回调函数（如果提供）
            if callback:
                try:
                    callback(latest_job, latest_job.state)
                except Exception as e:
                    logger.warning(f"回调函数执行失败: {e}")

            # 检查是否完成或失败 - 使用整数值比较更可靠
            state_value = latest_job.state if isinstance(latest_job.state, int) else latest_job.state.value if hasattr(latest_job.state, 'value') else int(latest_job.state)
            
            if state_value == 5:  # JOB_SUCCESS
                logger.info(f"✅ 管道 {pipeline_name} Job成功完成!")
                return latest_job.state, latest_job
            elif state_value == 4:  # JOB_FAILURE
                logger.error(f"❌ 管道 {pipeline_name} Job执行失败!")
                return latest_job.state, latest_job
            elif state_value == 6:  # JOB_KILLED
                logger.warning(f"⚠️ 管道 {pipeline_name} Job被终止!")
                return latest_job.state, latest_job
            
            # 如果未完成，则等待一段时间再轮询
            time.sleep(10)

        except Exception as e:
            logger.warning(f"轮询管道 {pipeline_name} Job状态时出错: {e}, 10秒后重试...")
            time.sleep(10)

    # 如果 while 循环正常结束，说明超时
    logger.warning(f"⏰ 管道 {pipeline_name} Job监听超时 ({timeout}s)")
    # 返回一个明确的未知状态和空的job_info
    return pps.JobState.JOB_STATE_UNKNOWN, None


def wait_for_pipeline_job_completion(client: pachyderm_sdk.Client, pipeline_name: str, timeout: int = 600):
    """等待管道Job完成的事件驱动版本
    
    Args:
        client: Pachyderm客户端
        pipeline_name: 管道名称
        timeout: 超时时间（秒）
        
    Returns:
        (success: bool, output_commit: pfs.Commit or None)
    """

    print("开始监听")
    logger.info(f"⏳ 等待管道 {pipeline_name} Job完成 (最大等待时间: {timeout}s)...")
    
    def job_callback(job_info, job_state):
        """在Job状态变化时的回调函数"""
        # 安全地获取状态名称
        state_name = get_state_name(job_state)
        logger.info(f"  📈 Job进度更新: {state_name}")
        
        # 显示数据处理进度（如果可用）
        if hasattr(job_info, 'data_processed') and hasattr(job_info, 'data_total'):
            if job_info.data_total > 0:
                progress = (job_info.data_processed / job_info.data_total) * 100
                logger.info(f"  📊 数据处理进度: {job_info.data_processed}/{job_info.data_total} ({progress:.1f}%)")
        
        # 显示其他状态信息 - 使用整数值比较
        state_value = job_state if isinstance(job_state, int) else job_state.value if hasattr(job_state, 'value') else int(job_state)
        
        if state_value == 3:  # JOB_RUNNING
            logger.info(f"  🔄 Job正在运行中...")
        elif state_value == 8:  # JOB_FINISHING
            logger.info(f"  🏁 Job即将完成...")
        elif state_value == 7:  # JOB_EGRESSING
            logger.info(f"  📤 Job正在输出结果...")
    
    logger.info(f"🛠️ 调用 monitor_pipeline_jobs 开始监听...")
    
    try:
        final_state, job_info = monitor_pipeline_jobs(
            client, pipeline_name, callback=job_callback, timeout=timeout
        )
        
        logger.info(f"📊 monitor_pipeline_jobs 返回: final_state={get_state_name(final_state)}, job_info={'available' if job_info else 'None'}")
        print(f"📊 monitor_pipeline_jobs 返回: final_state={get_state_name(final_state)}, job_info={'available' if job_info else 'None'}")
        
        if job_info is None:
            logger.error(f"❌ 管道 {pipeline_name} 监听失败，没有获取到Job信息")
            return False, None
        
        # 安全地检查最终状态
        state_value = final_state if isinstance(final_state, int) else final_state.value if hasattr(final_state, 'value') else int(final_state)
        
        if state_value == 5:  # JOB_SUCCESS
            # 获取输出commit
            if hasattr(job_info, 'output_commit') and job_info.output_commit:
                output_commit = job_info.output_commit
                logger.info(f"✅ 管道 {pipeline_name} 成功完成，输出commit: {output_commit.id}")
                return True, output_commit
            else:
                logger.warning(f"⚠️ 管道 {pipeline_name} 显示成功，但没有输出commit")
                # 尝试手动获取输出commit
                output_commit = get_pipeline_output_commit(client, pipeline_name)
                if output_commit:
                    logger.info(f"✅ 手动获取到输出commit: {output_commit.id}")
                    return True, output_commit
                else:
                    logger.error(f"❌ 无法获取管道 {pipeline_name} 的输出commit")
                    return False, None
        else:
            state_name = get_state_name(final_state)
            logger.error(f"❌ 管道 {pipeline_name} 执行失败，最终状态: {state_name}")
            return False, None
            
    except Exception as e:
        logger.error(f"等待管道 {pipeline_name} 完成时出错: {e}", exc_info=True)
        return False, None


def get_pipeline_output_commit(client: pachyderm_sdk.Client, pipeline_name: str):
    """获取管道的最新输出commit
    
    Args:
        client: Pachyderm客户端
        pipeline_name: 管道名称
        
    Returns:
        最新的输出commit，如果没有则返回None
    """
    try:
        # 检查输出仓库是否存在
        repo_info = client.pfs.inspect_repo(repo=pfs.Repo(name=pipeline_name))
        
        if repo_info.branches:
            # 查找master分支
            for branch in repo_info.branches:
                if branch.branch.name == "master":
                    master_commit = branch.head
                    logger.info(f"获取到管道 {pipeline_name} 的输出commit: {master_commit.id}")
                    return master_commit
        
        logger.warning(f"管道 {pipeline_name} 没有找到master分支或输出")
        return None
        
    except Exception as e:
        logger.error(f"获取管道 {pipeline_name} 输出commit失败: {e}")
        return None
