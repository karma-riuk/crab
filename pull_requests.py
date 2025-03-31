from collections import defaultdict
import argparse, os, subprocess, docker
from typing import Any, Callable
from github.Commit import Commit
from github.ContentFile import ContentFile
from github.PullRequest import PullRequest
from github.Repository import Repository
import pandas as pd
from github import Github, GithubException
from tqdm import tqdm
from datetime import datetime

from dataset import (
    Comment,
    Dataset,
    Dataset_new,
    DatasetEntry,
    DatasetEntry_new,
    FileData,
    FileData_new,
    Metadata,
    Metadata_new,
)
from errors import (
    CantCheckoutCommitError,
    CantEnsureFullHistoryError,
    CantFetchPRError,
    MultipleFilesError,
    NoDiffsAfterError,
    NoDiffsBeforeError,
    SetupException,
)
from handlers import HandlerException, get_build_handler
from utils import has_only_1_comment, move_github_logging_to_file, clone, run_git_cmd


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


def is_pull_good(pull: PullRequest, verbose: bool = False) -> bool:
    comments = pull.get_review_comments()
    if pull.user.type == "Bot" or comments.totalCount > 2:
        return False

    if comments.totalCount == 2:
        comment_list = list(comments)
        second_comment = comment_list[1]
        if second_comment.user.login != pull.user.login:
            return False

    return has_only_1_comment(pull.get_commits(), pull.get_review_comments(), verbose=verbose)


def ensure_full_history(repo_path: str) -> None:
    result = run_git_cmd(["rev-parse", "--is-shallow-repository"], repo_path)

    if result.stdout.strip() == "true":
        run_git_cmd(["fetch", "--unshallow"], repo_path)


def reset_repo_to_latest_commit(repo_path: str) -> None:
    current_branch = run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"], repo_path).stdout.strip()
    run_git_cmd(["reset", "--hard", current_branch], repo_path)


def get_diffs_before(repo: Repository, pr: PullRequest) -> dict[str, str]:
    comments = list(pr.get_review_comments())
    comments.sort(key=lambda comment: comment.created_at)
    first_comment = comments[0]
    try:
        return {
            file.filename: file.patch
            for file in repo.compare(pr.base.sha, first_comment.commit_id).files
        }
    except GithubException as e:
        raise NoDiffsBeforeError(e)


def get_diffs_after(repo: Repository, pr: PullRequest) -> dict[str, str]:
    comments = list(pr.get_review_comments())
    commits = list(pr.get_commits())
    comments.sort(key=lambda comment: comment.created_at)
    commits.sort(key=lambda commit: commit.commit.author.date)

    # remove from the commtis the ones that happened after the first comment
    first_comment = comments[0]
    for commit in commits[:]:
        if commit.commit.author.date > first_comment.created_at:
            commits.remove(commit)
    last_commit_before_comments = commits[-1]
    try:
        return {
            file.filename: file.patch
            for file in repo.compare(last_commit_before_comments.sha, pr.merge_commit_sha).files
        }
    except GithubException as e:
        raise NoDiffsAfterError(e)


def checkout(repo_path: str, pr: PullRequest) -> None:
    try:
        ensure_full_history(repo_path)
    except subprocess.CalledProcessError as e:
        raise CantEnsureFullHistoryError(e.stderr)

    try:
        run_git_cmd(["checkout", pr.merge_commit_sha], repo_path)
    except subprocess.CalledProcessError:
        try:
            run_git_cmd(["fetch", "origin", f"pull/{pr.number}/merge"], repo_path)
        except subprocess.CalledProcessError as e:
            raise CantFetchPRError(e.stderr)

        try:
            run_git_cmd(["checkout", pr.merge_commit_sha], repo_path)
        except subprocess.CalledProcessError as e:
            raise CantCheckoutCommitError(e.stderr)


def try_read_file(fname: str) -> str:
    if not os.path.exists(fname):
        return ""   # file was removed after the PR
    try:
        with open(fname, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        return "Binary file (from filesystem), to be ignored"
    except IsADirectoryError:
        return "File listed in PR is a directory (likely a submodule), to be ignored"


def get_files(pr: PullRequest, repo: Repository, repo_path: str) -> dict[str, FileData_new]:
    ret = {}
    for file in pr.get_files():
        try:
            contents = repo.get_contents(file.filename, ref=pr.base.sha)
            assert isinstance(
                contents, ContentFile
            ), f"Multiple files with the same name {file.filename} in base sha {pr.base.sha} ({contents})"
            contents_before = contents.decoded_content.decode()
        except AssertionError as e:
            raise MultipleFilesError(e)
        except UnicodeError as e:
            contents_before = "Binary content (from API), to be ignored"
        except Exception as e:
            contents_before = ""   # file didn't exist before the PR

        try:
            contents = repo.get_contents(file.filename, ref=pr.merge_commit_sha)
            assert isinstance(
                contents, ContentFile
            ), f"Multiple files with the same name {file.filename} in merge commit sha {pr.base.sha} ({contents})"
            contents_after = contents.decoded_content.decode()
        except AssertionError as e:
            raise MultipleFilesError(e)
        except UnicodeError as e:
            contents_after = "Binary content (from API), to be ignored"
        except Exception as e:
            checkout(repo_path, pr)
            contents_after = try_read_file(os.path.join(repo_path, file.filename))

        ret[file.filename] = FileData_new(
            is_code_related=file.filename.endswith('.java'),
            coverage={},
            content_before_pr=contents_before,
            content_after_pr=contents_after,
        )

    return ret


def get_comments(pr: PullRequest) -> list[Comment]:
    ret = []
    for comment in pr.get_review_comments():
        comment = Comment(
            body=comment.body,
            file=comment.path,
            from_=comment.start_line if comment.start_line else comment.line,
            to=comment.line,
        )
        if comment.from_ is None or comment.to is None:
            comment.to = comment.original_line
            comment.from_ = comment.original_start_line
        ret.append(comment)
    return ret


def process_pull(
    repo: Repository,
    pr: PullRequest,
    dataset: Dataset_new,
    repos_dir: str,
    cache: dict[str, dict[int, DatasetEntry_new]] = {},
):
    if pr.number in cache.get(repo.full_name, set()):
        dataset.entries.append(cache[repo.full_name][pr.number])
        return

    entry = DatasetEntry_new(
        metadata=Metadata_new(
            repo.full_name,
            pr.number,
            pr.title,
            pr.body,
            pr.merge_commit_sha,
            reason_for_failure="Was still being processed",
        ),
        files={},
        diffs_before={},
        comments=[],
        diffs_after={},
    )
    dataset.entries.append(entry)

    comments = list(pr.get_review_comments())
    assert len(comments) == 1
    comment = comments[0]
    commented_file_path = comment.path

    repo_path = os.path.join(repos_dir, repo.full_name)

    build_handler = None

    setup_steps = [
        (
            "Getting diffs before the first commit...",
            lambda: entry.diffs_before.update(get_diffs_before(repo, pr)),
        ),
        (
            "Getting diffs after the first commit...",
            lambda: entry.diffs_after.update(get_diffs_after(repo, pr)),
        ),
        ("Cloning the repo...", lambda: clone(repo.full_name, repos_dir)),
        (
            "Getting the files...",
            lambda: entry.files.update(get_files(pr, repo, repo_path)),
        ),
        (
            "Getting the comments...",
            lambda: entry.comments.extend(get_comments(pr)),
        ),
        ("Checkout out merge commit...", lambda: checkout(repo_path, pr)),
    ]

    with tqdm(total=len(setup_steps), desc="Setting up PR", leave=False) as pbar:
        for message, action in setup_steps:
            pbar.set_postfix(
                {
                    "doing": message,
                    "started at": datetime.now().strftime("%d/%m, %H:%M:%S"),
                }
            )
            try:
                action()
            except SetupException as e:
                entry.metadata.last_cmd_error_msg = str(e)
                entry.metadata.reason_for_failure = e.reason_for_failure
                entry.metadata.successful = False
                return
            pbar.update(1)

    try:
        build_handler = get_build_handler(repos_dir, repo.full_name)
        entry.metadata.build_system = build_handler.get_type()
        build_handler.set_client(docker_client)
    except SetupException as e:
        entry.metadata.last_cmd_error_msg = str(e)
        entry.metadata.reason_for_failure = e.reason_for_failure
        entry.metadata.successful = False
        return

    def _check_coverages():
        for coverage_file, coverage in build_handler.check_coverage(commented_file_path):
            entry.files[commented_file_path].coverage[coverage_file] = coverage

    steps = [
        ("Checking for tests...", build_handler.check_for_tests),
        ("Compiling...", build_handler.compile_repo),
        ("Running tests...", build_handler.test_repo),
        ("Generating coverage...", build_handler.generate_coverage_report),
        ("Checking coverage...", _check_coverages),
    ]

    with build_handler, tqdm(total=len(steps), desc="Processing PR", leave=False) as pbar:
        try:
            for message, action in steps:
                pbar.set_postfix(
                    {
                        "doing": message,
                        "started at": datetime.now().strftime("%d/%m, %H:%M:%S"),
                    }
                )
                action()
                pbar.update(1)
        except HandlerException as e:
            entry.metadata.last_cmd_error_msg = str(e)
            entry.metadata.reason_for_failure = e.reason_for_failure
            entry.metadata.successful = False
        finally:
            build_handler.clean_repo()
            reset_repo_to_latest_commit(repo_path)

    if entry.metadata.successful:
        entry.metadata.reason_for_failure = ""  # was set to 'still processing', since it's done being processed and was successful, there are no reasons for failure


def process_repo(
    repo_name: str,
    dataset: Dataset_new,
    repos_dir: str,
    cache: dict[str, dict[int, DatasetEntry_new]] = {},
):
    repo = g.get_repo(repo_name)
    if repo.full_name in cache:
        dataset.entries.extend(cache[repo.full_name].values())
        dataset.to_json(args.output)

    prs = repo.get_pulls(state="closed")

    n_good_prs = 0
    with tqdm(total=prs.totalCount, desc="Processing prs", leave=False) as pbar:
        for pr in prs:
            pbar.set_postfix({"pr": pr.number, "# new good found": n_good_prs})
            if pr.merged_at is None or not is_pull_good(pr):
                pbar.update(1)
                continue

            n_good_prs += 1
            process_pull(repo, pr, dataset, repos_dir, cache)
            dataset.to_json(args.output)
            pbar.update(1)


def process_repos(
    df: pd.DataFrame,
    dataset: Dataset_new,
    repos_dir: str,
    cache: dict[str, dict[int, DatasetEntry_new]] = {},
):
    """
    Processes the repos in the given csv file, extracting the good ones and
    creating the "triplets" for the dataset.

    Parameters:
    csv_file (str): The csv file containing the projects.
    dataset (Dataset): The dataset in which the triplets will be stored.
        Passing it by reference in order have the latest information, in case of an error
    verbose (bool): Whether to be verbose or not
    """
    with tqdm(total=len(df), desc="Processing repos") as pbar:
        for _, row in df.iterrows():
            repo_name = row["name"]
            assert isinstance(repo_name, str)
            pbar.set_postfix(
                {
                    "repo": repo_name,
                    "started at": datetime.now().strftime("%d/%m, %H:%M:%S"),
                    "# triplets": f"{len(dataset)}/{len(dataset.entries)} ({len(dataset)/len(dataset.entries) if len(dataset.entries) > 0 else 0:.2%})",
                }
            )
            process_repo(repo_name, dataset, repos_dir, cache)
            pbar.update(1)


def only_inject_jacoco(
    dataset: Dataset_new,
    repos_dir: str,
    cache: dict[str, dict[int, DatasetEntry_new]] = {},
):
    n_successfull_injections = 0
    n_tried_injections = 0
    with tqdm(cache, desc="Processing repos (only for injection") as top_bar:
        for repo_name in top_bar:
            top_bar.set_postfix(
                {
                    "# successfull injections": f"{n_successfull_injections}/{n_tried_injections} ({n_successfull_injections/n_tried_injections if n_tried_injections > 0 else 0:.2%})"
                }
            )
            with tqdm(total=len(cache[repo_name]), desc=f"Processing prs", leave=False) as pbar:
                # extracting keys so that it doesn't get messy as I pop elements from the dict
                pr_numbers = list(cache[repo_name].keys())
                for pr_number in pr_numbers:
                    pbar.set_postfix({"repo": repo_name, "pr": pr_number})

                    entry = cache[repo_name].pop(pr_number)
                    if entry.metadata.reason_for_failure != "Couldn't execute jacoco":
                        dataset.entries.append(entry)
                        dataset.to_json(args.output)
                        pbar.update(1)
                        continue

                    n_tried_injections += 1
                    repo = g.get_repo(repo_name)
                    pull = repo.get_pull(pr_number)
                    process_pull(repo, pull, dataset, repos_dir, cache)
                    pbar.update(1)
                    last_addition = dataset.entries[-1]
                    last_metadata = last_addition.metadata
                    if (
                        last_metadata.repo == repo_name
                        and last_metadata.pr_number == pr_number
                        and last_metadata.successful
                    ):
                        n_successfull_injections += 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Creates the triplets for the CRAB dataset.')
    parser.add_argument(
        'csv_file',
        type=str,
        help='The csv file containing the projects (the results from clone_repos.py).',
    )
    parser.add_argument(
        '-o',
        '--output',
        type=str,
        default="./dataset.json",
        help='The file in which the dataset will be contained. Default is "./dataset.json"',
    )
    parser.add_argument(
        '-r',
        '--repos',
        type=str,
        default="./results/",
        help='The directory in which the repos were cloned (will be cloned if they aren\'t there already). Default: "./results/"',
    )
    parser.add_argument(
        '-c',
        '--cache',
        type=str,
        help="The name of the output file from another run of this script. This is for when the script unexpectedly got interrupted and you want to resume from where you left off.",
    )
    # parser.add_argument('-v', '--verbose', action='store_true', help='Prints the number of good projects.')
    parser.add_argument(
        "--only-repo",
        type=str,
        help="If this argument is not provided, all the repos in the '--repos' csv will be processed. If instead you want to run the script on a single repo (for testing purposes mainly) provide a string of form 'XXX/YYY' to this argument, where XXX is the owner of the repo and YYY is the name of the repo",
    )
    parser.add_argument(
        "--only-inject-jacoco",
        action="store_true",
        help="You must provide a cache with --cache. It will take that cache and go through all the entries that failed because they couldn't execute jacoco and process them again, trying to inject jacoco manually",
    )

    args = parser.parse_args()
    g = Github(os.environ["GITHUB_AUTH_TOKEN_CRAB"])
    docker_client = docker.from_env()
    move_github_logging_to_file()

    df = get_good_projects(args.csv_file)

    if args.only_repo is not None:
        df = df.loc[df["name"] == args.only_repo]

    cache: dict[str, dict[int, DatasetEntry_new]] = defaultdict(dict)
    if args.cache is not None:
        cache_dataset = Dataset_new.from_json(args.cache)
        for cache_entry in cache_dataset.entries:
            cache[cache_entry.metadata.repo][cache_entry.metadata.pr_number] = cache_entry

    dataset = Dataset_new()
    try:
        if args.only_inject_jacoco:
            only_inject_jacoco(dataset, args.repos, cache)
        else:
            process_repos(df, dataset, args.repos, cache)
    finally:
        dataset.to_json(args.output)
