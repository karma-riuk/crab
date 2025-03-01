import pandas as pd
import argparse, os, sys, subprocess, docker
from tqdm import tqdm
import shutil

tqdm.pandas()

EXCLUSION_LIST = [
    "edmcouncil/idmp", # requires authentication
    "aosp-mirror/platform_frameworks_base", # takes ages to clone
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

def get_build_file(root: str, repo: str, updates: dict, verbose: bool = False):
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

    to_keep = ["pom.xml", "build.gradle", "build.xml"]
    for entry in os.scandir(path):
        if entry.is_file() and entry.name in to_keep:
            if verbose: print(f"Found {entry.name} in {repo} root, so keeping it and returning")
            updates["depth_of_build_file"] = 0
            return os.path.join(path, entry.name)
    
    # List files in the immediate subdirectories
    for entry in os.scandir(path):
        if entry.is_dir():
            for sub_entry in os.scandir(entry.path):
                if sub_entry.is_file() and sub_entry.name in to_keep:
                    if verbose: print(f"Found {sub_entry.name} in {repo} first level, so keeping it and returning")
                    updates["depth_of_build_file"] = 1
                    return os.path.join(path, entry.name, sub_entry.name)

    updates["error_msg"] = "No build file found"
    return None

def has_tests(path: str, build_file: str, updates: dict) -> bool:
    with open(build_file, "r") as f:
        content = f.read()

        for library in ["junit", "testng", "mockito"]:
            if library in content:
                updates["detected_source_of_tests"] = library + " library in build file"
                return True

        for keyword in ["testImplementation", "functionalTests", "bwc_tests_enabled"]:
            if keyword in content:
                updates["detected_source_of_tests"] = keyword + " keyword in build file"
                return False

    test_dirs = [
        "src/test/java",
        "src/test/kotlin",
        "src/test/groovy",
        "test",
    ]
    for td in test_dirs:
        if os.path.exists(os.path.join(path, td)):
            updates["detected_source_of_tests"] = td + " dir exists in repo"
            return True

    updates["error_msg"] = "No tests found"
    return False

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

def execute_in_container(container, command):
    exec_result = container.exec_run(command, stream=True)
    output = "".join([line.decode() for line in exec_result.output])
    return exec_result.exit_code, output

def compile_repo(build_file: str, container, updates: dict) -> bool:
    """
    Attempts to compile a repository inside a running Docker container.
    """
    if build_file.endswith("pom.xml") or build_file.endswith("build.xml"):
        build_cmd = "mvn clean compile"
    elif build_file.endswith("build.gradle"):
        build_cmd = "gradle compileJava"
    else:
        updates["error_msg"] = "Unsupported build system for compiling: " + build_file
        return False
    
    exit_code, output = execute_in_container(container, build_cmd)
    if exit_code != 0:
        updates["compiled_successfully"] = False
        updates["error_msg"] = output
        return False
    
    updates["compiled_successfully"] = True
    return True

def test_repo(build_file: str, container, updates: dict) -> bool:
    if build_file.endswith("pom.xml") or build_file.endswith("build.xml"):
        test_cmd = "mvn clean compile"
    elif build_file.endswith("build.gradle"):
        test_cmd = "gradle compileJava"
    else:
        updates["error_msg"] = "Unsupported build system for testing: " + build_file
        return False
    
    exit_code, output = execute_in_container(container, test_cmd)
    if exit_code != 0:
        updates["tested_successfully"] = False
        updates["error_msg"] = output
        return False
    
    updates["tested_successfully"] = True
    updates["error_msg"] = output

    return True

def clean_repo(build_file: str, container):
    if build_file.endswith("pom.xml") or build_file.endswith("build.xml"):
        clean_cmd = "mvn clean"
    elif build_file.endswith("build.gradle"):
        clean_cmd = "gradle clean"
    else:
        return
    
    container.exec_run(clean_cmd)

def process_row(repo, client, dest: str, updates: dict, force: bool = False, verbose: bool = False) -> None:
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

        pbar.set_postfix_str("Getting build file...")
        build_file = get_build_file(dest, repo, updates)
        if build_file is None:
            if verbose: print(f"Removing {repo}, no build file")
            remove_dir(repo_path)
            return
        pbar.update(1)
        
        pbar.set_postfix_str("Checking for tests...")
        if not has_tests(repo_path, build_file, updates):
            if verbose: print(f"Removing {repo}, no test suites")
            remove_dir(repo_path)
            return
        if verbose: print(f"Keeping {repo}")
        pbar.update(1)

        container = client.containers.run(
            image="crab-java-env",
            command="tail -f /dev/null",
            volumes={os.path.abspath(repo_path): {"bind": "/repo", "mode": "rw"}},
            detach=True,
            tty=True
        )

        try: 
            pbar.set_postfix_str("Compiling...")
            compiled = compile_repo(build_file, container, updates)
            if not compiled:
                if verbose: print(f"Removing {repo}, failed to compile")
                clean_repo(build_file, container)
                remove_dir(repo_path)
                return
            pbar.update(1)

            pbar.set_postfix_str("Running tests...")
            tested = test_repo(build_file, container, updates)
            clean_repo(build_file, container)
            if not tested:
                if verbose: print(f"Removing {repo}, failed to run tests")
                remove_dir(repo_path)
                return
            pbar.update(1)

            # If repo was not removed, then it is a good repo
            updates["good_repo_for_crab"] = True
        finally:
            container.kill()
            container.remove()

def save_df_with_updates(df, updates_list, verbose=False):
    # Create columns for the new data
    df = df.assign(
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
        n_tests_skipped=None,
        good_repo_for_crab=False,
        error_msg=None,
    )

   # Set the new data
    for index, updates in updates_list:
        for col, value in updates.items():
            df.at[index, col] = value  # Batch updates to avoid fragmentation

    if verbose: print("Writing results...")
    df.to_csv("results.csv", index=False)

def clone_repos(file: str, dest: str, force: bool =False, verbose: bool = False) -> None:
    """
    Download the repos listed in the file passed as argument. The downloaded repos will be placed in the folder that is named as the dest argument.


    Arguments:
        file (str): The name of the file to download the repos from. Must be a .csv.gz file (downloaded from https://seart-ghs.si.usi.ch)
        dest (str): The name of the root directory in which to download the repos
        verbose (bool): If `True`, outputs detailed process information. Defaults to `False`.
    """
    if verbose: print(f"Reading CSV file {file}")
    df = pd.read_csv(file)

    # drop all columns besides the name
    df = df[["name"]]

    updates_list = []  # Collect updates in a list
    client = docker.from_env()

    good_repos = 0
    try:
        if verbose: print("Processing repositories")
        with tqdm(total=len(df)) as pbar:
            for i, row in df.iterrows():
                pbar.set_postfix({"repo": row["name"], "good_repos": good_repos})
                updates = {}
                updates_list.append((i, updates))  # Collect updates
                process_row(row["name"], client, dest, updates, force=force, verbose=verbose)
                if "good_repo_for_crab" in updates and updates["good_repo_for_crab"]:
                    good_repos += 1
                pbar.update(1)
    except KeyboardInterrupt:
        print("Interrupted by user, saving progress...")
        save_df_with_updates(df, updates_list, verbose=verbose)
    except Exception as e:
        print("An error occured, saving progress and then raising the error...")
        save_df_with_updates(df, updates_list, verbose=verbose)
        raise e


if __name__ == "__main__":
    # whtie the code to parse the arguments here
    parser = argparse.ArgumentParser(description="Clone repos from a given file")
    parser.add_argument("file", default="results.csv.gz", help="The file to download the repos from. Default is 'results.csv.gz'")
    parser.add_argument("-d", "--dest", default="./results/", help="The root directory in which to download the repos. Default is './results/'")
    parser.add_argument("-f", "--force", action="store_true", help="Force the download of the repos")
    parser.add_argument("-v", "--verbose", action="store_true", help="Make the program verbose")
    args = parser.parse_args()

    clone_repos(args.file, args.dest, force=args.force, verbose=args.verbose)

