import os, sys, logging, subprocess
from datetime import datetime
from github.Commit import Commit
from github.PaginatedList import PaginatedList
from github.PullRequestComment import PullRequestComment
from tqdm import tqdm

from errors import CantCloneRepoError


def move_github_logging_to_file():
    github_logger = logging.getLogger("github")

    # Remove existing handlers to prevent duplicate logging
    for handler in github_logger.handlers[:]:
        github_logger.removeHandler(handler)

    file_handler = logging.FileHandler("github_api.log")  # Log to file
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    github_logger.addHandler(file_handler)
    github_logger.propagate = False  # Prevent logging to standard output


def parse_date(date: str) -> datetime:
    return datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")


def has_only_1_round_of_comments(
    commits: PaginatedList[Commit], comments: PaginatedList[PullRequestComment]
):
    if comments is None or commits is None or comments.totalCount == 0 or commits.totalCount == 0:
        return False

    commit_dates = [
        commit.commit.author.date
        for commit in tqdm(
            commits, total=commits.totalCount, desc="Extracting date from commits", leave=False
        )
    ]
    comment_dates = [
        comment.created_at
        for comment in tqdm(
            comments, total=comments.totalCount, desc="Extracting date from comments", leave=False
        )
    ]

    commit_dates.sort()
    comment_dates.sort()

    first_comment_time = comment_dates[0]
    last_comment_time = comment_dates[-1]

    n_before = n_after = 0
    for commit_time in tqdm(commit_dates, desc="Checking for 1 round of comments", leave=False):
        if commit_time < first_comment_time:
            n_before += 1
            continue
        if commit_time > last_comment_time:
            n_after += 1
            continue

        if first_comment_time < commit_time < last_comment_time:
            return False

    return n_before >= 1 and n_after >= 1


def has_only_1_comment(
    commits: PaginatedList[Commit],
    comments: PaginatedList[PullRequestComment],
    verbose: bool = False,
):
    if comments is None or commits is None or comments.totalCount == 0 or commits.totalCount == 0:
        if verbose:
            print(
                f"No comments or commits: {comments.totalCount} comments, {commits.totalCount} commits"
            )
        return False

    if comments.totalCount != 1:
        if verbose:
            print(f"More than 1 comment: {comments.totalCount} comments")
        return False

    commit_dates = [commit.commit.author.date for commit in commits]

    comment_date = comments[0].created_at

    n_before = n_after = 0
    for commit_date in commit_dates:
        if commit_date < comment_date:
            n_before += 1
            continue
        if commit_date > comment_date:
            n_after += 1
            continue
    if verbose:
        print(f"n_before: {n_before}, n_after: {n_after}")
    return n_before >= 1 and n_after >= 1


def is_already_repo_cloned(repos_dir: str, repo_name: str) -> bool:
    """
    Checks if the repository is cloned locally and if its remote URL matches the expected GitHub repository URL.

    Parameters:
    repos_dir (str): The directory where repositories are stored.
    repo_name (str): The name of the repository.

    Returns:
    bool: True if the repository is already cloned, False otherwise.
    """
    path = os.path.join(repos_dir, repo_name)

    if not os.path.exists(path) or not os.path.isdir(path):
        return False

    try:
        result = subprocess.run(
            ["git", "-C", path, "remote", "-v"], capture_output=True, text=True, check=True
        )

        remote_urls = result.stdout.splitlines()
        expected_url = f"https://github.com/{repo_name}"

        return any(expected_url in url for url in remote_urls)

    except subprocess.CalledProcessError:
        return False


def clone(repo: str, dest: str, force: bool = False) -> None:
    """
    Clone a GitHub repository to the specified destination directory.

    Parameters:
        repo (str): The name of the repository to clone (e.g., "user/repo").
        dest (str): The destination directory where the repository will be cloned.
        force (bool): If True, force clone even if the repository already exists.

    Raises:
        CantCloneRepoError: If the repository cannot be cloned.
    """
    local_repo_path = os.path.join(dest, repo)
    if not force and is_already_repo_cloned(dest, repo):
        return

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", f"https://github.com/{repo}", local_repo_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise CantCloneRepoError(e.stderr)


def run_git_cmd(cmd: list[str], repo_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_path] + cmd,
        check=True,
        capture_output=True,
        text=True,
    )


def prompt_yes_no(prompt: str, *, default: bool | None = None) -> bool:
    choices = "y/n"
    if default is not None:
        choices = "Y/n" if default else "y/N"

    while True:
        ans = input(f"{prompt} [{choices}]: ").strip().lower()
        if ans in {"y", "yes"}:
            return True
        elif ans in {"n", "no"}:
            return False
        elif default is not None:
            return default
        else:
            print("Please enter 'y' or 'n'.")
