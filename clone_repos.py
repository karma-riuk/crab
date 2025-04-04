import pandas as pd
import argparse, os, docker
from tqdm import tqdm
import shutil
from datetime import datetime

from handlers import FailedToCompileError, FailedToTestError, NoTestsFoundError, NoTestResultsToExtractError, get_build_handler
from utils import clone

tqdm.pandas()

EXCLUSION_LIST = [
    "edmcouncil/idmp", # requires authentication
    "aosp-mirror/platform_frameworks_base", # takes ages to clone
    "alibaba/druid", # tests takes literally more than 5 hours
    "hashgraph/hedera-mirror-node", # requires authentication
    "Starcloud-Cloud/starcloud-llmops", # requires authentication
]

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
            try:
                pbar.set_postfix_str("Checking for tests...")
                build_handler.check_for_tests()
                pbar.update(1)

                pbar.set_postfix_str("Compiling...")
                build_handler.compile_repo()
                updates["compiled_successfully"] = True
                pbar.update(1)

                pbar.set_postfix_str("Running tests...")
                build_handler.test_repo()
                updates["tested_successfully"] = True
                pbar.update(1)

                build_handler.clean_repo()

                # If repo was not removed, then it is a good repo
                updates["good_repo_for_crab"] = True
            except NoTestsFoundError as e:
                updates["error_msg"] = str(e)
                if verbose: print(f"Removing {repo}, error: no tests found")
                remove_dir(repo_path)
                return
            except FailedToCompileError as e:
                updates["error_msg"] = str(e)
                updates["compiled_successfully"] = False
                if verbose: print(f"Removing {repo}, error: failed to compile")
                remove_dir(repo_path)
                return
            except FailedToTestError as e:
                updates["error_msg"] = str(e)
                updates["tested_successfully"] = False
                if verbose: print(f"Removing {repo}, error: failed to run tests")
                remove_dir(repo_path)
                return
            except NoTestResultsToExtractError as e:
                updates["error_msg"] = str(e)
                if verbose: print(f"Removing {repo}, error: failed to extract test results")
                remove_dir(repo_path)
                return


def save_df_with_updates(df, updates_list, results_file: str, verbose=False):
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

    updates_list = []  # Collect updates in a list
    client = docker.from_env()

    good_repos = 0
    n_processed = 0
    last_i_saved = -1
    to_be_processed = df
    if lazy and results_df is not None:
        df = results_df.copy()
        only_processed = results_df[results_df["processed"]]
        good_repos = only_processed[only_processed["good_repo_for_crab"] == True]["good_repo_for_crab"].sum()
        n_processed = len(only_processed)
        last_i_saved = n_processed
        to_be_processed = df.loc[~df["name"].isin(only_processed["name"])] # the .loc is to have a view of df and not to make a copy (a copy resets the index and we don't want that)
    try:
        if verbose: print("Processing repositories")
        with tqdm(total=len(df)) as pbar:
            pbar.update(n_processed)
            for i, row in to_be_processed.iterrows():
                if i % 10 == 0:
                    save_df_with_updates(df, updates_list, results_file, verbose=verbose)
                    last_i_saved = i
                pbar.set_postfix({
                    "repo": row["name"],
                    "last index saved": last_i_saved,
                    "# good repos": f"{good_repos} ({good_repos/n_processed if n_processed > 0 else 0:.2%})", 
                    "time": datetime.now().strftime("%H:%M:%S")
                })
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
    parser.add_argument("-r", "--results", default="repos.csv", help="The name of file in which to save the results. Also used with --continue. Default is 'repos.csv'")
    parser.add_argument("-l", "--lazy", action="store_true", help="If given, the program will continue from where it left off, by not touch the already processed repos. Will look at the file pointed by the --results argument")
    parser.add_argument("-f", "--force", action="store_true", help="Force the download of the repos")
    parser.add_argument("-v", "--verbose", action="store_true", help="Make the program verbose")
    args = parser.parse_args()

    process_repos(args.file, args.dest, args.results, lazy=args.lazy, force=args.force, verbose=args.verbose)

