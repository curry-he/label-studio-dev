import random
from typing import List, Dict, Tuple

def split_dataset(tasks: List[Dict], train_split: float = 0.7, validation_split: float = 0.2, test_split: float = 0.1) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Splits a list of tasks into training, validation, and test sets.

    :param tasks: A list of tasks (dictionaries) to be split.
    :param train_split: The proportion of the dataset to allocate to the training set.
    :param validation_split: The proportion of the dataset to allocate to the validation set.
    :param test_split: The proportion of the dataset to allocate to the test set.
    :return: A tuple containing the training, validation, and test sets.
    """
    if not (0 <= train_split <= 1 and 0 <= validation_split <= 1 and 0 <= test_split <= 1):
        raise ValueError("Split proportions must be between 0 and 1.")

    if train_split + validation_split + test_split > 1.0:
        raise ValueError("The sum of split proportions cannot exceed 1.")

    shuffled_tasks = list(tasks)
    random.shuffle(shuffled_tasks)

    total_tasks = len(shuffled_tasks)
    train_end = int(total_tasks * train_split)
    validation_end = train_end + int(total_tasks * validation_split)

    train_set = shuffled_tasks[:train_end]
    validation_set = shuffled_tasks[train_end:validation_end]
    test_set = shuffled_tasks[validation_end:]

    return train_set, validation_set, test_set