from typing import Optional
from github.PullRequest import PullRequest
from github.Repository import Repository
import pandas as pd
import argparse, os
from github import Github
from tqdm import tqdm
from datetime import datetime

from dataset import Dataset, DatasetEntry, FileData, Metadata, Diff
from utils import has_only_1_comment, move_github_logging_to_file


def get_good_projects(csv_file: str) -> pd.DataFrame:
    """
    Extracts the good (the ones that compile and test successfully, and that
    have at least one test) from the given file.
    
    Parameters:
    csv_file (str): The csv file containing the projects.

    Returns:
    pd.DataFrame: The good projects.
    """
    df = pd.read_csv(csv_file)
    return df.loc[(df['good_repo_for_crab'] == True) & (df['n_tests'] > 0)]

def is_pull_good(pull: PullRequest, verbose: bool = False):
    return has_only_1_comment(pull.get_commits(), pull.get_review_comments(), verbose=verbose)

def get_good_prs(repo: Repository, stats_df: Optional[pd.DataFrame]) -> list[PullRequest]:
    good_prs = []
    prs = repo.get_pulls(state="closed")

    if stats_df is None or repo.full_name not in stats_df["repo"].unique():
        potenially_good_prs = prs
        number_of_prs = prs.totalCount
        from_cached = False
    else:
        potenially_good_prs_numbers = stats_df.loc[(stats_df["repo"] == repo.full_name) & (stats_df["has_only_1_comment"] == True)]["pr_number"]
        potenially_good_prs = [repo.get_pull(n) for n in potenially_good_prs_numbers]
        number_of_prs = len(potenially_good_prs)
        from_cached = True

    if number_of_prs == 0:
        return []

    with tqdm(total=number_of_prs, desc=f"Extracting good PRs from {repo.full_name}", leave=False) as pbar:
        for pr in potenially_good_prs:
            pbar.set_postfix({"found": len(good_prs), "pr_number": pr.number, "from_cached": from_cached})
            if pr.merged_at is None:
                pbar.update(1)
                continue
            if is_pull_good(pr):
                good_prs.append(pr)
            pbar.update(1)

    return good_prs

def process_pull(repo: Repository, pr: PullRequest, dataset: Dataset):
    commits = list(pr.get_commits())
    if not commits:
        return  # No commits, skip processing

    first_commit = commits[0]
    last_commit = commits[-1]

    diffs_before = [Diff(file.filename, file.patch) for file in repo.compare(pr.base.sha, first_commit.sha).files]

    comments = list(pr.get_review_comments())
    assert len(comments) == 1
    comment_text = comments[0].body if comments else ""

    diffs_after = [Diff(file.filename, file.patch) for file in repo.compare(first_commit.sha, last_commit.sha).files]

    dataset.entries.append(DatasetEntry(
        metadata=Metadata(repo.full_name, pr.number, pr.merge_commit_sha, True),
        files=[FileData(file.filename) for file in pr.get_files()],
        diffs_before=diffs_before,
        comment=comment_text,
        diffs_after=diffs_after,
    ))

def process_repo(repo_name: str, stats_df: Optional[pd.DataFrame], dataset: Dataset):
    good_prs = []
    repo = g.get_repo(repo_name)
    good_prs = get_good_prs(repo, stats_df)

    for pr in tqdm(good_prs, desc="Processing good prs", leave=False):
        process_pull(repo, pr, dataset)

def process_repos(csv_file: str, stats_csv: Optional[str], dataset: Dataset):
    """
    Processes the repos in the given csv file, extracting the good ones and
    creating the "triplets" for the dataset.

    Parameters:
    csv_file (str): The csv file containing the projects.
    dataset (Dataset): The dataset in which the triplets will be stored.
        Passing it by reference in order have the latest information, in case of an error
    verbose (bool): Whether to be verbose or not
    """
    df = get_good_projects(csv_file)
    stats_df = pd.read_csv(stats_csv) if stats_csv is not None else None
    already_processed_repos = []
    potentially_good_repos = []
    if stats_df is not None:
        already_processed_repos = stats_df["repo"].unique()
        potentially_good_repos = stats_df.loc[stats_df["has_only_1_comment"]]["repo"].unique()

    with tqdm(total=len(df), desc="Processing repos") as pbar:
        for _, row in df.iterrows():
            repo_name = row["name"]
            assert isinstance(repo_name, str)
            pbar.set_postfix({
                "repo": repo_name, 
                "started at": datetime.now().strftime("%d/%m, %H:%M:%S"),
                "# triplets": len(dataset)
            })
            if repo_name in already_processed_repos and repo_name not in potentially_good_repos:
                pbar.update(1)
                continue # skipping because we know there's nothing good already
            process_repo(repo_name, stats_df, dataset)
            pbar.update(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Creates the triplets for the CRAB dataset.')
    parser.add_argument('csv_file', type=str, help='The csv file containing the projects (the results from clone_repos.py).')
    parser.add_argument('-o', '--output', type=str, default="./dataset.json", help='The file in which the dataset will be contained. Default is "./dataset.json"')
    parser.add_argument('-s', '--stats', type=str, help="The name of the output file from the stats_pull_requests.py. The stats file already knows which PRs are good (the ones with only 1 comment between two rounds of commits), so instead of going through all of PRs of a repo, we can fast-track using this. If the repo isn't in the stats file, we must go through each PR")
    # parser.add_argument('-v', '--verbose', action='store_true', help='Prints the number of good projects.')

    args = parser.parse_args()
    g = Github(os.environ["GITHUB_AUTH_TOKEN_CRAB"])
    move_github_logging_to_file()

    dataset = Dataset()
    try:
        # try and finally to save, regardless of an error occuring or the program finished correctly
        process_repos(args.csv_file, args.stats, dataset)
    finally:
        dataset.to_json(args.output)
