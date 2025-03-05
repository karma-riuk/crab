import pandas as pd
import argparse, os, sys, subprocess, docker
from tqdm import tqdm
import shutil
from typing import Optional
from datetime import datetime

from handlers import GradleHandler, MavenHandler, BuildHandler

tqdm.pandas()

EXCLUSION_LIST = [
    "edmcouncil/idmp", # requires authentication
    "aosp-mirror/platform_frameworks_base", # takes ages to clone
    "alibaba/druid", # tests takes literally more than 5 hours
]

def clone(repo: str, dest: str, updates: dict, force: bool = False, verbose: bool = False) -> None:
    """
    Clones a GitHub repository into a local directory.

    Args:
        repo (str): The repository to clone, in the format "owner/repo_name".
        force (bool, optional): If `True`, re-clones the repository even if it already exists. Defaults to `False`.
    """
    local_repo_path = os.path.join(dest, repo)
    if not force and os.path.exists(local_repo_path):
        # if verbose: print(f"Skipping {repo}, already exists")
        updates["cloned_successfully"] = "Already exists"
        return 

    if verbose: print(f"Cloning {repo}")
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", f"https://github.com/{repo}", local_repo_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if proc.returncode != 0:
        updates["cloned_successfully"] = False
        print(f"Failed to clone {repo}", file=sys.stderr)
        print(f"Error message was:", file=sys.stderr)
        error_msg = proc.stderr.decode()
        print(error_msg, file=sys.stderr)
        updates["error_msg"] = error_msg
    else:
        updates["cloned_successfully"] = True

def get_build_handler(root: str, repo: str, updates: dict, verbose: bool = False) -> Optional[BuildHandler]:
    """
    Get the path to the build file of a repository. The build file is either a
    `pom.xml`, `build.gradle`, or `build.xml` file.

    Args:
        root (str): The root directory in which the repository is located.
        repo (str): The name of the repository.

    Returns:
        str | None: The path to the repository if it is valid, `None` otherwise
    """
    path = os.path.join(root, repo)
    # Check if the given path is a directory
    if not os.path.isdir(path):
        error_msg = f"The path {path} is not a valid directory."
        print(error_msg, file=sys.stderr)
        updates["error_msg"] = error_msg
        return None

    to_keep = ["pom.xml", "build.gradle"]
    for entry in os.scandir(path):
        if entry.is_file() and entry.name in to_keep:
            if verbose: print(f"Found {entry.name} in {repo} root, so keeping it and returning")
            updates["depth_of_build_file"] = 0
            if entry.name == "build.gradle":
                updates["build_system"] = "gradle"
                return GradleHandler(path, entry.name, updates)
            else:
                updates["build_system"] = "maven"
                return MavenHandler(path, entry.name, updates)
    
    # List files in the immediate subdirectories
    for entry in os.scandir(path):
        if entry.is_dir():
            for sub_entry in os.scandir(entry.path):
                if sub_entry.is_file() and sub_entry.name in to_keep:
                    if verbose: print(f"Found {sub_entry.name} in {repo} first level, so keeping it and returning")
                    updates["depth_of_build_file"] = 1
                    if entry.name == "build.gradle":
                        updates["build_system"] = "gradle"
                        return GradleHandler(path, os.path.join(entry.name, sub_entry.name), updates)
                    else:
                        updates["build_system"] = "maven"
                        return MavenHandler(path, os.path.join(entry.name, sub_entry.name), updates)

    updates["error_msg"] = "No build file found"
    return None

def remove_dir(dir: str) -> None:
    """
    Removes a directory and all its contents. Removes parent directorie if it is empty after removing child (dir).

    Args:
        dir (str): The directory to remove.
    """
    shutil.rmtree(dir)
    parent = os.path.abspath(os.path.join(dir, os.path.pardir))
    if os.listdir(parent) == []:
        shutil.rmtree(parent)

def process_row(repo, client, dest: str, updates: dict, force: bool = False, verbose: bool = False) -> None:
    updates["good_repo_for_crab"] = False
    updates["processed"] = True
    with tqdm(total=5, leave=False) as pbar:
        if repo in EXCLUSION_LIST:
            updates["error_msg"] = "Repo in exclusion list"
            if verbose: print(f"Skipping {repo}, in exclusion list")
            return

        pbar.set_postfix_str("Cloning...")
        if force:
            clone(repo, dest, updates, verbose=verbose)
        pbar.update(1)

        repo_path = os.path.join(dest, repo)
        if not os.path.exists(repo_path):
            updates["error_msg"] = "Repo not cloned"
            return

        pbar.set_postfix_str("Getting build handler...")
        build_handler = get_build_handler(dest, repo, updates)
        if build_handler is None:
            if verbose: print(f"Removing {repo}, no build file")
            remove_dir(repo_path)
            return
        pbar.update(1)

        build_handler.set_client(client)
        with build_handler:
            pbar.set_postfix_str("Checking for tests...")
            if not build_handler.has_tests():
                if verbose: print(f"Removing {repo}, no test suites")
                remove_dir(repo_path)
                return
            if verbose: print(f"Keeping {repo}")
            pbar.update(1)

            pbar.set_postfix_str("Compiling...")
            if not build_handler.compile_repo():
                if verbose: print(f"Removing {repo}, failed to compile")
                remove_dir(repo_path)
                return
            pbar.update(1)

            pbar.set_postfix_str("Running tests...")
            if not build_handler.test_repo():
                if verbose: print(f"Removing {repo}, failed to run tests")
                remove_dir(repo_path)
                return
            build_handler.clean_repo()
            pbar.update(1)

            # If repo was not removed, then it is a good repo
            updates["good_repo_for_crab"] = True

def save_df_with_updates(df, updates_list, results_file: str, verbose=False):
    # Create columns for the new data
    df = df.assign(
        processed=False,
        cloned_successfully=None,
        build_system=None,
        depth_of_build_file=None,
        detected_source_of_tests=None,
        compiled_successfully=None,
        tested_successfully=None,
        n_tests=None,
        n_tests_with_grep=None,
        n_tests_passed=None,
        n_tests_failed=None,
        n_tests_errors=None,
        n_tests_skipped=None,
        good_repo_for_crab=None,
        error_msg=None,
    )

   # Set the new data
    for index, updates in updates_list:
        for col, value in updates.items():
            df.at[index, col] = value  # Batch updates to avoid fragmentation

    if verbose: print("Writing results...")
    df.to_csv(results_file, index=False)

def process_repos(file: str, dest: str, results_file: str, /, lazy: bool = False, force: bool =False, verbose: bool = False) -> None:
    """
    Download the repos listed in the file passed as argument. The downloaded repos will be placed in the folder that is named as the dest argument.


    Arguments:
        file (str): The name of the file to download the repos from. Must be a .csv.gz file (downloaded from https://seart-ghs.si.usi.ch)
        dest (str): The name of the root directory in which to download the repos
        verbose (bool): If `True`, outputs detailed process information. Defaults to `False`.
    """
    if verbose: print(f"Reading CSV file {file}")
    df = pd.read_csv(file)
    results_df = pd.read_csv(results_file) if lazy else None

    # drop all columns besides the name
    df = df[["name"]]

    updates_list = []  # Collect updates in a list
    client = docker.from_env()

    good_repos = 0
    n_processed = 0
    last_i_saved = -1
    try:
        if verbose: print("Processing repositories")
        with tqdm(total=len(df)) as pbar:
            for i, row in df.iterrows():
                if i % 10 == 0:
                    save_df_with_updates(df, updates_list, results_file, verbose=verbose)
                    last_i_saved = i
                pbar.set_postfix({
                    "repo": row["name"],
                    "last index saved": last_i_saved,
                    "# good repos": f"{good_repos} ({good_repos/n_processed if n_processed > 0 else 0:.2%})", 
                    "time": datetime.now().strftime("%H:%M:%S")
                })
                if lazy:
                    already_processed_row = results_df[results_df["name"] == row["name"]].iloc[0]
                    already_processed = already_processed_row["processed"]
                    if already_processed: # row was already processed
                        pbar.update(1)
                        n_processed += 1
                        updates_list.append((i, dict(already_processed_row))) 
                        good_repos += 1 if already_processed_row["good_repo_for_crab"] else 0
                        continue
                updates = {}
                updates_list.append((i, updates))
                process_row(row["name"], client, dest, updates, force=force, verbose=verbose)
                if "good_repo_for_crab" in updates and updates["good_repo_for_crab"]:
                    good_repos += 1
                pbar.update(1)
                n_processed += 1
    except KeyboardInterrupt as e:
        print("Interrupted by user, saving progress...")
        save_df_with_updates(df, updates_list, results_file, verbose=verbose)
        raise e
    except Exception as e:
        print("An error occured, saving progress and then raising the error...")
        save_df_with_updates(df, updates_list, results_file, verbose=verbose)
        raise e

    if verbose: print("Saving results...")
    save_df_with_updates(df, updates_list, results_file, verbose=verbose)

if __name__ == "__main__":
    # whtie the code to parse the arguments here
    parser = argparse.ArgumentParser(description="Clone repos from a given file")
    parser.add_argument("file", default="results.csv.gz", help="The file to download the repos from. Default is 'results.csv.gz'")
    parser.add_argument("-d", "--dest", default="./results/", help="The root directory in which to download the repos. Default is './results/'")
    parser.add_argument("-r", "--results", default="results.csv", help="The name of file in which to save the results. Also used with --continue. Default is 'results.csv'")
    parser.add_argument("-l", "--lazy", action="store_true", help="If given, the program will continue from where it left off, by not touch the already processed repos. Will look at the file pointed by the --results argument")
    parser.add_argument("-f", "--force", action="store_true", help="Force the download of the repos")
    parser.add_argument("-v", "--verbose", action="store_true", help="Make the program verbose")
    args = parser.parse_args()

    process_repos(args.file, args.dest, args.results, lazy=args.lazy, force=args.force, verbose=args.verbose)

