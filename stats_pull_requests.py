import argparse, re, statistics
from collections import defaultdict
from dataset import Dataset
from utils import EnumChoicesAction
from enum import Enum


def distrib_of_prs_per_repo(dataset: Dataset):
    repo2pr = defaultdict(int)
    for entry in dataset.entries:
        repo2pr[entry.metadata.repo] += 1
    for repo, num_pr in repo2pr.items():
        print(f"{repo} {num_pr}")


def count_entries(dataset: Dataset):
    print(f"Total entries in dataset: {len(dataset.entries)}")


def distrib_of_prs_per_repo_covered(dataset: Dataset):
    repo2pr: dict[str, int] = defaultdict(int)
    for entry in dataset.entries:
        if entry.metadata.is_covered:
            repo2pr[entry.metadata.repo] += 1
    for repo, num_pr in repo2pr.items():
        print(f"{repo} {num_pr}")


def biggest_repo_comment_gen(dataset: Dataset):
    N = 5
    repo2pr: dict[str, int] = defaultdict(int)
    for entry in dataset.entries:
        repo2pr[entry.metadata.repo] += 1

    total = sum(repo2pr.values())
    top_n = sorted(repo2pr, key=lambda e: repo2pr.get(e, 0), reverse=True)[:N]

    print("Repo with larget number of PRs for comment gen:")
    print('\n'.join([f"{repo}: {repo2pr[repo]} ({repo2pr[repo]/total:.2%})" for repo in top_n]))


def biggest_repo_refinement(dataset: Dataset):
    N = 5
    repo2pr: dict[str, int] = defaultdict(int)
    for entry in dataset.entries:
        if entry.metadata.is_covered:
            repo2pr[entry.metadata.repo] += 1

    total = sum(repo2pr.values())
    top_n = sorted(repo2pr, key=lambda e: repo2pr.get(e, 0), reverse=True)[:N]

    print("Repo with larget number of PRs for refinement:")
    print('\n'.join([f"{repo}: {repo2pr[repo]} ({repo2pr[repo]/total:.2%})" for repo in top_n]))


def count_tokens(comment: str):
    return len(re.findall(r'\w+', comment))


def tokens_per_comment(dataset: Dataset):
    all_counts = [count_tokens(entry.comments[0].body) for entry in dataset.entries]
    print('\n'.join([str(i) for i in all_counts]))
    return
    ntoken2count: dict[int, int] = defaultdict(int)
    for entry in dataset.entries:
        ntoken2count[count_tokens(entry.comments[0].body)] += 1

    for k, v in ntoken2count.items():
        print(f"{k} {v}")


def tokens_quartiles(dataset: Dataset):
    all_counts = [count_tokens(entry.comments[0].body) for entry in dataset.entries]
    q1, q2, q3 = statistics.quantiles(all_counts)
    print(f"Min {min(all_counts)}")
    print(f"Q1 = {q1}, Median = {q2}, Q3 = {q3}")
    print(f"Max {max(all_counts)}")


def diff_before_sizes(dataset: Dataset):
    all_counts = [
        sum(len(diff.splitlines()) if diff else 0 for diff in entry.diffs_before.values())
        for entry in dataset.entries
        if entry.metadata.is_covered
    ]
    print('\n'.join([str(i) for i in all_counts]))
    return
    diffsize2count: dict[int, int] = defaultdict(int)
    for entry in dataset.entries:
        diff_size = sum(
            len(diff.splitlines()) if diff else 0 for diff in entry.diffs_before.values()
        )
        diffsize2count[diff_size] += 1

    for k, v in diffsize2count.items():
        print(f"{k} {v}")


def diff_before_quartiles(dataset: Dataset):
    all_counts = [
        sum(len(diff.splitlines()) if diff else 0 for diff in entry.diffs_before.values())
        for entry in dataset.entries
    ]
    q1, q2, q3 = statistics.quantiles(all_counts)
    print(f"Min {min(all_counts)}")
    print(f"Q1 = {q1}, Median = {q2}, Q3 = {q3}")
    print(f"Max {max(all_counts)}")


def n_files_before(dataset: Dataset):
    all_counts = [
        sum(1 if diff else 0 for diff in entry.diffs_before.values())
        for entry in dataset.entries
        if entry.metadata.is_covered
    ]
    print('\n'.join([str(i) for i in all_counts]))
    return
    nfiles2count: dict[int, int] = defaultdict(int)
    for entry in dataset.entries:
        n_files = sum(1 if diff else 0 for diff in entry.diffs_before.values())
        nfiles2count[n_files] += 1

    for k, v in nfiles2count.items():
        print(f"{k} {v}")


def diff_after_sizes(dataset: Dataset):
    all_counts = [
        sum(len(diff.splitlines()) if diff else 0 for diff in entry.diffs_after.values())
        for entry in dataset.entries
        if entry.metadata.is_covered
    ]
    print('\n'.join([str(i) for i in all_counts]))
    return
    diffsize2count: dict[int, int] = defaultdict(int)
    for entry in dataset.entries:
        if entry.metadata.is_covered:
            diff_size = sum(
                len(diff.splitlines()) if diff else 0 for diff in entry.diffs_after.values()
            )
            diffsize2count[diff_size] += 1

    for k, v in diffsize2count.items():
        print(f"{k} {v}")


def n_files_after(dataset: Dataset):
    all_counts = [
        sum(1 if diff else 0 for diff in entry.diffs_after.values())
        for entry in dataset.entries
        if entry.metadata.is_covered
    ]
    print('\n'.join([str(i) for i in all_counts]))
    return
    nfiles2count: dict[int, int] = defaultdict(int)
    for entry in dataset.entries:
        if entry.metadata.is_covered:
            n_files = sum(1 if diff else 0 for diff in entry.diffs_after.values())
            nfiles2count[n_files] += 1

    for k, v in nfiles2count.items():
        print(f"{k} {v}")


def diff_after_sizes_selected(dataset: Dataset):
    all_counts = [
        sum(len(diff.splitlines()) if diff else 0 for diff in entry.diffs_after.values())
        for entry in dataset.entries
        if entry.metadata.is_covered
        if entry.metadata.selection and entry.metadata.selection.diff_after_address_change
    ]
    print('\n'.join([str(i) for i in all_counts]))
    return
    diffsize2count: dict[int, int] = defaultdict(int)
    for entry in dataset.entries:
        if entry.metadata.is_covered:
            if entry.metadata.selection and entry.metadata.selection.diff_after_address_change:
                diff_size = sum(
                    len(diff.splitlines()) if diff else 0 for diff in entry.diffs_after.values()
                )
                diffsize2count[diff_size] += 1

    for k, v in diffsize2count.items():
        print(f"{k} {v}")


def n_files_after_selected(dataset: Dataset):
    all_counts = [
        sum(1 if diff else 0 for diff in entry.diffs_after.values())
        for entry in dataset.entries
        if entry.metadata.is_covered
        if entry.metadata.selection and entry.metadata.selection.diff_after_address_change
    ]
    print('\n'.join([str(i) for i in all_counts]))
    return

    nfiles2count: dict[int, int] = defaultdict(int)
    for entry in dataset.entries:
        if entry.metadata.is_covered:
            if entry.metadata.selection and entry.metadata.selection.diff_after_address_change:
                n_files = sum(1 if diff else 0 for diff in entry.diffs_after.values())
                nfiles2count[n_files] += 1

    for k, v in nfiles2count.items():
        print(f"{k} {v}")


class Action(Enum):
    COUNT = ("count", count_entries)
    DISTRIB = ("distrib", distrib_of_prs_per_repo)
    DISTRIB_COVERED = ("distrib_covered", distrib_of_prs_per_repo_covered)
    BIGGEST_REPO_COMMENT_GEN = ("biggest_repo_comment_gen", biggest_repo_comment_gen)
    BIGGEST_REPO_REFINEMENT = ("biggest_repo_refinement", biggest_repo_refinement)
    TOKENS = ("tokens", tokens_per_comment)
    TOKENS_QUARTILES = ("tokens_quartiles", tokens_quartiles)
    DIFF_BEFORE_SIZES = ("diff_before_sizes", diff_before_sizes)
    DIFF_BEFORE_QUARTILES = ("diff_before_quartiles", diff_before_quartiles)
    N_FILES_BEFORE = ("n_files_before", n_files_before)
    DIFF_AFTER_SIZES = ("diff_after_sizes", diff_after_sizes)
    N_FILES_AFTER = ("n_files_after", n_files_after)
    DIFF_AFTER_SIZES_SELECTED = ("diff_after_sizes_selected", diff_after_sizes_selected)
    N_FILES_AFTER_SELECTED = ("n_files_after_selected", n_files_after_selected)

    def __new__(cls, value, func):
        # This __new__ assigns the “value” for each member (for argparse/choices),
        # and also stashes the function pointer into a .func attribute.
        obj = object.__new__(cls)
        obj._value_ = value
        obj.func = func   # type: ignore
        return obj

    def perform(self, dataset):
        # Simply call the stored function, passing the dataset.
        return self.func(dataset)   # type: ignore


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Creates the triplets for the CRAB dataset.')
    parser.add_argument(
        'dataset',
        type=str,
        help='The dataset to extract data from',
    )
    parser.add_argument(
        'action',
        type=Action,
        action=EnumChoicesAction,
        help='Action to perform on the data',
    )

    args = parser.parse_args()

    args.action.perform(Dataset.from_json(args.dataset))
