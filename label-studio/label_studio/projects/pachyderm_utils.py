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
            auth_token='257ff1a708284e9582fe3ac0a34f7864',
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
    """
    
    with client.pfs.commit(branch=pfs.Branch.from_uri(f"{repo_name}@master")) as commit:
        # Always commit the config first
        commit.put_file_from_bytes(path="/config.json", data=json.dumps(config_for_pfs).encode('utf-8'))
        logger.info(f"Committed config.json to repo '{repo_name}'")
        
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
        commit.put_file_from_bytes(
            path="/config.json", 
            data=json.dumps(config, indent=2, ensure_ascii=False).encode('utf-8')
        )
        logger.info(f"提交处理配置到仓库 '{repo_name}', commit: {commit.id}")
        return commit

def create_smart_match_pipeline_spec(raw_images_repo: str, annotations_repo: str, output_repo: str, processing_config: dict) -> dict:
    """
    创建智能匹配模式的管道规范
    通过标注数据中的原始文件名信息匹配图片和标注进行处理
    
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
        "description": "智能匹配模式：通过标注数据中的原始文件名信息匹配图片和标注",
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
    return pipeline_spec

def create_format_converter_pipeline_spec(input_repo: str, output_repo: str) -> dict:
    """
    创建格式转换管道规范
    将数据集处理输出转换为YOLO格式
    
    Args:
        input_repo: 输入仓库名（来自ls-dataset管道的输出）
        output_repo: 输出仓库名
    
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
        "description": f"格式转换管道：将 {input_repo} 的输出转换为YOLO格式",
        "input": {
            "pfs": {
                "repo": input_repo,
                "glob": "/"
            }
        },
        "transform": {
            "image": "localhost:5000/format-converter:latest",
            "cmd": [
                "python",
                "/app/converter.py",
                "--input_dir", "/pfs/" + input_repo,
                "--output_dir", "/pfs/out"
            ]
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
                        files = list(client.pfs.list_file(commit=master_commit, path="/"))
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
    result_file = client.pfs.get_file(commit=output_commit, path=file_path)
    result_content = result_file.read().decode('utf-8')
    logger.info(f"Result content: {result_content}")
    return json.loads(result_content)

def list_files_in_commit(client: pachyderm_sdk.Client, commit: pfs.Commit) -> list:
    """Lists all files in a given commit."""
    try:
        file_list = []
        for file_info in client.pfs.list_file(commit=commit, path="/"):
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
        for file_info in client.pfs.list_file(commit=version_commit, path="/"):
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
