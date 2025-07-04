import random
from tasks.models import Task
from .models import VersionTask

def auto_split_tasks(version, split_ratio=(0.8, 0.1, 0.1), task_queryset=None):
    """
    自动将任务划分到 train/valid/test，并写入 VersionTask。
    :param version: DatasetVersion 实例
    :param split_ratio: (train, valid, test) 比例，和为1
    :param task_queryset: 可选，指定要划分的任务QuerySet，否则用该项目下所有任务
    """
    if task_queryset is None:
        task_queryset = Task.objects.filter(project=version.project)
    tasks = list(task_queryset)
    random.shuffle(tasks)
    total = len(tasks)
    train_end = int(total * split_ratio[0])
    valid_end = train_end + int(total * split_ratio[1])
    train_tasks = tasks[:train_end]
    valid_tasks = tasks[train_end:valid_end]
    test_tasks = tasks[valid_end:]

    # 清理旧的划分
    VersionTask.objects.filter(version=version).delete()

    # 批量写入
    VersionTask.objects.bulk_create([
        VersionTask(version=version, task=task, subset='train') for task in train_tasks
    ] + [
        VersionTask(version=version, task=task, subset='valid') for task in valid_tasks
    ] + [
        VersionTask(version=version, task=task, subset='test') for task in test_tasks
    ])
    return {
        'train': len(train_tasks),
        'valid': len(valid_tasks),
        'test': len(test_tasks),
        'total': total,
    } 