from collections import defaultdict
import argparse, os, subprocess, docker, uuid, sys, traceback
from concurrent.futures import wait, FIRST_COMPLETED, ProcessPoolExecutor, Future
from github.Commit import Commit
from github.ContentFile import ContentFile
from github.PullRequest import PullRequest
from github.Repository import Repository
import pandas as pd
from github import Github, GithubException
from pandas.io.common import tarfile
import requests
from tqdm import tqdm
from datetime import datetime

from dataset import (
    ArchiveState,
    Comment,
    Dataset,
    DatasetEntry,
    FileData,
    Metadata,
)
from errors import (
    CantCheckoutCommitError,
    CantEnsureFullHistoryError,
    CantFetchPRError,
    CommentedFileNotInOriginalChanges,
    MultipleFilesError,
    NoDiffsAfterError,
    NoDiffsBeforeError,
    NoLinesForCommentError,
    SetupException,
)
from handlers import HandlerException, get_build_handler
from utils import has_only_1_comment, move_logger_to_file, clone, run_git_cmd

EXCLUSION_LIST = [
    "edmcouncil/idmp",  # requires authentication
    "aosp-mirror/platform_frameworks_base",  # takes ages to clone
    "alibaba/druid",  # tests takes literally more than 5 hours
    "hashgraph/hedera-mirror-node",  # requires authentication
    "Starcloud-Cloud/starcloud-llmops",  # requires authentication
]


def is_pull_good(pull: PullRequest, verbose: bool = False) -> bool:
    if pull.merged_at is None:
        return False

    comments = pull.get_review_comments()
    if pull.user.type == "Bot" or comments.totalCount > 2 or comments.totalCount == 0:
        return False

    if comments.totalCount == 2:
        comment_1, comment_2 = sorted(comments, key=lambda c: c.created_at)
        if comment_2.user is None:
            return False

        if comment_1.user is not None and not (
            comment_2.in_reply_to_id == comment_1.id  # is reply
            and comment_2.user.id == pull.user.id  # from the author of the PR
        ):
            return False

    return has_only_1_comment(pull.get_commits(), pull.get_review_comments(), verbose=verbose)


def ensure_full_history(repo_path: str) -> None:
    result = run_git_cmd(["rev-parse", "--is-shallow-repository"], repo_path)

    if result.stdout.strip() == "true":
        run_git_cmd(["fetch", "--unshallow"], repo_path)


def reset_repo_to_latest_commit(repo_path: str) -> None:
    current_branch = run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"], repo_path).stdout.strip()
    run_git_cmd(["reset", "--hard", current_branch], repo_path)


def get_last_commit_before_comments(pr: PullRequest) -> Commit:
    comments = list(pr.get_review_comments())
    commits = list(pr.get_commits())
    comments.sort(key=lambda comment: comment.created_at)
    commits.sort(key=lambda commit: commit.commit.author.date)

    # remove from the commtis the ones that happened after the first comment
    first_comment = comments[0]
    for commit in commits[:]:
        if commit.commit.author.date > first_comment.created_at:
            commits.remove(commit)
    return commits[-1]


def get_diffs_before(repo: Repository, pr: PullRequest) -> dict[str, str]:
    last_commit_before_comments = get_last_commit_before_comments(pr)
    try:
        return {
            file.filename: file.patch
            for file in repo.compare(pr.base.sha, last_commit_before_comments.sha).files
        }
    except GithubException as e:
        raise NoDiffsBeforeError(e)


def get_diffs_after(repo: Repository, pr: PullRequest) -> dict[str, str]:
    last_commit_before_comments = get_last_commit_before_comments(pr)
    try:
        return {
            file.filename: file.patch
            for file in repo.compare(last_commit_before_comments.sha, pr.merge_commit_sha).files
        }
    except GithubException as e:
        raise NoDiffsAfterError(e)


def checkout(repo_path: str, sha: str, pr_number: int) -> None:
    try:
        ensure_full_history(repo_path)
    except subprocess.CalledProcessError as e:
        raise CantEnsureFullHistoryError(e.stderr)

    try:
        run_git_cmd(["checkout", "-f", sha], repo_path)
    except subprocess.CalledProcessError:
        try:
            run_git_cmd(["fetch", "origin", f"pull/{pr_number}/merge"], repo_path)
        except subprocess.CalledProcessError as e:
            raise CantFetchPRError(e.stderr)

        try:
            run_git_cmd(["checkout", "-f", sha], repo_path)
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


def get_files(pr: PullRequest, repo: Repository, repo_path: str) -> dict[str, FileData]:
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
            checkout(repo_path, pr.merge_commit_sha, pr.number)
            contents_after = try_read_file(os.path.join(repo_path, file.filename))

        ret[file.filename] = FileData(
            is_code_related=file.filename.endswith('.java'),
            coverage={},
            content_before_pr=contents_before,
            content_after_pr=contents_after,
        )

    return ret


def get_comments(pr: PullRequest) -> list[Comment]:
    ret = []
    filenames = {file.filename for file in pr.get_files()}
    for comment in pr.get_review_comments():
        if comment.path not in filenames:
            raise CommentedFileNotInOriginalChanges(f"File {comment.path} not in {filenames}")
        if (
            comment.start_line is None
            and comment.original_start_line is None
            and comment.line is None
            and comment.original_line is None
        ):
            raise NoLinesForCommentError(
                f"Github gave a comment with no lines what so ever {comment}"
            )

        from_ = comment.start_line
        if from_ is None:
            from_ = comment.original_start_line

        to = comment.line
        if to is None:
            to = comment.original_line

        comment_to_add = Comment(
            body=comment.body,
            file=comment.path,
            from_=from_,
            to=to,
        )
        if comment_to_add.from_ is None and comment_to_add.to is None:
            raise NoLinesForCommentError(
                "After creating the comment object, the from_ an to fields were None"
            )
        ret.append(comment_to_add)
    return ret


def archive_repo(repo_path: str, metadata: Metadata, destination: str, state: ArchiveState) -> None:
    """
    Archives the repo at the specified path, including only the files tracked by git.
    The archive is stored in the destination directory with a filename based on the PR number.
    """
    if not os.path.exists(destination):
        os.makedirs(destination)

    archive_name = metadata.archive_name(state)
    archive_path = os.path.join(destination, archive_name)

    result = run_git_cmd(["ls-files"], repo_path)
    tracked_files = result.stdout.strip().split("\n")

    with tarfile.open(archive_path, "w:gz") as tar:
        for file in tracked_files:
            full_path = os.path.join(repo_path, file)
            if os.path.exists(full_path):
                tar.add(full_path, arcname=file)


def process_pull(
    repo: Repository,
    pr: PullRequest,
    dataset: Dataset,
    repos_dir: str,
    archive_destination: str,
    cache: dict[str, dict[int, DatasetEntry]] = {},
    show_progress: bool = True,
):
    if pr.number in cache.get(repo.full_name, set()):
        dataset.entries.append(cache[repo.full_name][pr.number])
        return

    if not is_pull_good(pr):
        return

    metadata = Metadata(
        uuid.uuid4().hex,
        repo.full_name,
        pr.number,
        pr.title,
        pr.body,
        pr.merge_commit_sha,
        reason_for_failure="Was still being processed",
    )
    entry = DatasetEntry(
        metadata=metadata,
        files={},
        diffs_before={},
        comments=[],
        diffs_after={},
    )
    dataset.entries.append(entry)

    comments = pr.get_review_comments()
    assert comments.totalCount == 1
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
        ("Checkout out base commit...", lambda: checkout(repo_path, pr.base.sha, pr.number)),
        (
            "Archiving the repo...",
            lambda: archive_repo(repo_path, metadata, archive_destination, ArchiveState.BASE),
        ),
        (
            "Checkout out merge commit...",
            lambda: checkout(repo_path, pr.merge_commit_sha, pr.number),
        ),
        (
            "Archiving the repo...",
            lambda: archive_repo(repo_path, metadata, archive_destination, ArchiveState.MERGED),
        ),
    ]

    pbar = tqdm(
        total=len(setup_steps) + 6, desc="Processing PR", leave=False, disable=not show_progress
    )
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
        pbar.set_postfix(
            {
                "doing": "Setting up build handler...",
                "started at": datetime.now().strftime("%d/%m, %H:%M:%S"),
            }
        )
        build_handler = get_build_handler(repos_dir, repo.full_name)
        pbar.update(1)
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

    if all(not comment.file.endswith(".java") for comment in entry.comments):
        # if the commented files are all not code related, why bother compiling and testing the code?
        pbar.update(5)
        entry.metadata.is_code_related = False
        metadata.successful = True
        entry.metadata.reason_for_failure = "Valid PR! But isn't code related though."
        return

    entry.metadata.is_code_related = True
    with build_handler:
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
        else:
            entry.metadata.successful = True
        finally:
            build_handler.clean_repo()
            reset_repo_to_latest_commit(repo_path)

    commented_files = [comment.file for comment in entry.comments]
    is_covered = True
    for file_name in commented_files:
        coverage = entry.files[file_name].coverage
        if len(coverage) == 0 or all(value == 0 for value in coverage.values()):
            is_covered = False
            break

    entry.metadata.is_covered = is_covered

    if entry.metadata.successful:
        if entry.metadata.is_covered:
            entry.metadata.reason_for_failure = "Valid PR!"
        else:
            entry.metadata.reason_for_failure = "Valid PR! But not covered :("


def process_repo(
    repo_name: str,
    dataset: Dataset,
    repos_dir: str,
    archive_destination: str,
    cache: dict[str, dict[int, DatasetEntry]] = {},
    position: int = 1,
    show_progress: bool = True,
):
    repo = g.get_repo(repo_name)
    already_seen_prs = set()
    if repo.full_name in cache:
        already_seen_prs = set(cache[repo.full_name].keys())

    prs = repo.get_pulls(state="closed")

    with tqdm(
        total=prs.totalCount,
        desc=f"Processing prs of {repo_name}",
        leave=False,
        position=position,
        unit="PR",
    ) as pbar:
        for pr in prs:
            pbar.set_postfix({"pr": pr.number})
            try:
                if pr.number in already_seen_prs:
                    continue

                process_pull(
                    repo, pr, dataset, repos_dir, archive_destination, cache, show_progress
                )
                # dataset.to_json(args.output)
            except (requests.exceptions.RetryError, requests.exceptions.ReadTimeout) as r_e:
                tqdm.write(f"[ERROR] {type(r_e)}: {r_e}")
            except Exception as e:
                exc_type, _, exc_tb = sys.exc_info()
                assert exc_tb is not None
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                print(exc_type, fname, exc_tb.tb_lineno)
                tqdm.write(
                    f"[ERROR] PR #{pr.number} in {repo.full_name}. {exc_type} in {fname} at {exc_tb.tb_lineno}: {e}"
                )
                tqdm.write(traceback.format_exc())
            finally:
                pbar.update(1)


# Wrapper to run in each worker process
def process_repo_worker(
    repo_name: str, repos_dir: str, archive_destination: str, cache: dict, position: int
) -> list:
    # Local dataset to collect entries for this repo
    local_dataset = Dataset()

    # Call the existing process_repo, but passing the local GitHub and Docker clients
    # You may need to modify process_repo to accept g and docker_client as parameters
    try:
        process_repo(
            repo_name,
            local_dataset,
            repos_dir,
            archive_destination,
            cache,
            position=position,
            show_progress=False,
        )
    finally:
        return local_dataset.entries


def process_repos_parallel(
    df: pd.DataFrame,
    dataset: Dataset,
    repos_dir: str,
    archive_destination: str,
    n_workers: int,
    cache: dict[str, dict[int, DatasetEntry]] = {},
):
    """
    Parallel processing of repos using ProcessPoolExecutor.

    Parameters:
        df: DataFrame with a 'name' column of repos to process
        dataset: Shared Dataset to collect all entries
        repos_dir: Directory root for cloned repos
        archive_destination: Directory for archives
        cache: Optional cache of previously processed PR entries
    """
    if len(cache) > 0:
        for pr2entry in tqdm(list(cache.values()), desc="Adding cache in dataset"):
            dataset.entries.extend(pr2entry.values())
        print(f"Saving dataset to {args.output}...", end=" ", flush=True)
        dataset.to_json(args.output)
        print("Done")

    repo_names = [repo_name for repo_name in df["name"] if repo_name not in EXCLUSION_LIST]
    free_positions = list(range(1, n_workers + 1))
    repo_names_iter = iter(repo_names)
    future_to_repo: dict[Future, tuple[str, int]] = {}
    with tqdm(
        total=len(repo_names),
        desc="Processing repos",
        unit="repo",
    ) as outer_pb, ProcessPoolExecutor(max_workers=n_workers) as executor:
        # Map each repo to a future

        for _ in range(n_workers):
            try:
                name = next(repo_names_iter)
            except StopIteration:
                break
            pos = free_positions.pop(0)
            fut = executor.submit(
                process_repo_worker, name, repos_dir, archive_destination, cache, pos
            )
            future_to_repo[fut] = (name, pos)

        try:
            while future_to_repo:
                done, _ = wait(future_to_repo, return_when=FIRST_COMPLETED)
                for fut in done:
                    repo_finished, pos = future_to_repo.pop(fut)
                    outer_pb.update(1)
                    entries = fut.result()
                    if len(entries) > 0:
                        dataset.entries.extend(entries)
                        dataset.to_json(args.output)

                    try:
                        name = next(repo_names_iter)
                    except StopIteration:
                        # no more tasks: free the slot
                        free_positions.append(pos)
                    else:
                        new_fut = executor.submit(
                            process_repo_worker, name, repos_dir, archive_destination, cache, pos
                        )
                        future_to_repo[new_fut] = (name, pos)
        except BaseException as top_e:
            print("\n" * n_workers)
            print(f"[ERROR] {type(top_e)}: {top_e}")
            print("Saving all the entries of repos that were still being processed")
            dataset_ids = {entry.metadata.id for entry in dataset.entries}
            # any futures that happen
            for fut in list(future_to_repo):
                try:
                    result = fut.result()
                    print(f"Saving {len(result)} for {future_to_repo[fut][0]}")
                    for entry in result:
                        if entry.metadata.id in dataset_ids:
                            print(
                                f"{entry.metadata.repo} PR #{entry.metadata.pr_number} already in dataset"
                            )
                    dataset.entries.extend(result)
                except Exception as bot_e:
                    print(f"[ERROR] {type(bot_e)}: {bot_e}")
                    pass
            # re-raise so the top‐level finally block still runs
            raise


def process_repos(
    df: pd.DataFrame,
    dataset: Dataset,
    repos_dir: str,
    archive_destination: str,
    cache: dict[str, dict[int, DatasetEntry]] = {},
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
    if len(cache) > 0:
        for pr2entry in tqdm(list(cache.values()), desc="Adding cache in dataset"):
            dataset.entries.extend(pr2entry.values())
        dataset.to_json(args.output)

    with tqdm(total=len(df), desc="Processing repos", unit="repo") as pbar:
        for _, row in df.iterrows():
            repo_name = row["name"]
            assert isinstance(repo_name, str)
            if repo_name in EXCLUSION_LIST:
                pbar.update(1)
                continue
            pbar.set_postfix(
                {
                    "repo": repo_name,
                    "started at": datetime.now().strftime("%d/%m, %H:%M:%S"),
                    "# triplets": f"{len(dataset)}/{len(dataset.entries)} ({len(dataset)/len(dataset.entries) if len(dataset.entries) > 0 else 0:.2%})",
                }
            )
            process_repo(repo_name, dataset, repos_dir, archive_destination, cache)
            pbar.update(1)


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
        metavar="OUTPUT_FILE_PATH",
        type=str,
        default="./dataset.json",
        help='The file in which the dataset will be contained. Default is "./dataset.json"',
    )
    parser.add_argument(
        '-r',
        '--repos',
        type=str,
        metavar="REPOS_DIR_ROOT",
        default="./results/",
        help='The directory in which the repos were cloned (will be cloned if they aren\'t there already). Default: "./results/"',
    )
    parser.add_argument(
        '-c',
        '--cache',
        metavar="CACHE_FILE_PATH",
        type=str,
        help="The name of the output file from another run of this script. This is for when the script unexpectedly got interrupted and you want to resume from where you left off.",
    )
    parser.add_argument(
        "-a",
        "--archive-destination",
        type=str,
        metavar="ARCHIVE_DIR_ROOT",
        default="./dataset/archives",
        help="The directory in which the repos will be archived. Default is './dataset/archives'.",
    )
    parser.add_argument(
        "-s",
        "--sort-by",
        metavar="COLUMN_NAME",
        type=str,
        help="Sort the incoming csv by the given column. If not set, keep the original csv ordering",
    )
    # parser.add_argument('-v', '--verbose', action='store_true', help='Prints the number of good projects.')
    parser.add_argument(
        "--only-repo",
        type=str,
        metavar="OWNER/NAME",
        help="Run the script on a single repo (format: 'owner/name'). If not set, all repos in '--repos' CSV are processed.",
    )
    parser.add_argument(
        "--cache-requests",
        action="store_true",
        help="Caches GitHub API requests in a SQLite file using 'requests_cache' (see optional-requirements.txt). Useful for faster reruns if the script crashes or you’re tweaking it. Might produce stale data.",
    )
    parser.add_argument(
        "--max-workers",
        metavar="N_WORKERS",
        type=int,
        help="Parallelize the processing of the repos with the given number of workers. If not given, the script is monothreaded",
    )

    args = parser.parse_args()

    if args.cache_requests:
        import requests_cache

        requests_cache.install_cache(
            'github_cache',
            expire_after=requests_cache.NEVER_EXPIRE,
            wal=True,
            check_same_thread=False,
        )
        move_logger_to_file("requests_cache", "requests_cache.log")

    github_api_token = os.environ.get("GITHUB_AUTH_TOKEN_CRAB")
    if github_api_token is None:
        print(
            "[WARNING] The enviorment variable GITHUB_AUTH_TOKEN_CRAB was not set. This isn't critical, but it will significantly limit the number of GitHub requests this script can make."
        )

    g = Github(github_api_token, seconds_between_requests=0)

    docker_client = docker.from_env()
    move_logger_to_file("github", "github_api.log")

    # df = get_good_projects(args.csv_file)
    df = pd.read_csv(args.csv_file)

    sort_column = args.sort_by
    if sort_column is not None:
        if sort_column not in df.columns:
            raise ValueError(f"Column '{sort_column}' not present in given csv file")
        df.sort_values(sort_column, inplace=True, ascending=False)

    if args.only_repo is not None:
        df = df.loc[df["name"] == args.only_repo]

    cache: dict[str, dict[int, DatasetEntry]] = defaultdict(dict)
    if args.cache is not None:
        cache_dataset = Dataset.from_json(args.cache)
        for cache_entry in cache_dataset.entries:
            cache[cache_entry.metadata.repo][cache_entry.metadata.pr_number] = cache_entry

    dataset = Dataset()
    try:
        if args.max_workers is not None:
            process_repos_parallel(
                df, dataset, args.repos, args.archive_destination, args.max_workers, cache
            )
        else:
            process_repos(df, dataset, args.repos, args.archive_destination, cache)
    finally:
        print(f"Writing dataset to {args.output}")
        dataset.to_json(args.output)
