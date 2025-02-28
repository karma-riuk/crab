import pandas as pd
import argparse, os, sys, subprocess
from tqdm import tqdm
import shutil
from pandarallel import pandarallel
import multiprocessing

tqdm.pandas()

EXCLUSION_LIST = [
    "edmcouncil/idmp",
]

def clone(repo: str, dest: str, force: bool = False, verbose: bool = False) -> None:
    """
    Clones a GitHub repository into a local directory.

    Args:
        repo (str): The repository to clone, in the format "owner/repo_name".
        force (bool, optional): If `True`, re-clones the repository even if it already exists. Defaults to `False`.
    """
    local_repo_path = os.path.join(dest, repo)
    if not force and os.path.exists(local_repo_path):
        # if verbose: print(f"Skipping {repo}, already exists")
        return 
    if verbose: print(f"Cloning {repo}")
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", f"https://github.com/{repo}", local_repo_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if proc.returncode != 0:
        print(f"Failed to clone {repo}", file=sys.stderr)
        print(f"Error message was:", file=sys.stderr)
        print(proc.stderr.decode(), file=sys.stderr)

def get_build_file(root: str, repo: str, verbose: bool = False):
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
        print(f"The path {path} is not a valid directory.", file=sys.stderr)
        return None

    to_keep = ["pom.xml", "build.gradle", "build.xml"]
    for entry in os.scandir(path):
        if entry.is_file() and entry.name in to_keep:
            if verbose: print(f"Found {entry.name} in {repo} root, so keeping it and returning")
            return os.path.join(path, entry.name)
    
    # List files in the immediate subdirectories
    for entry in os.scandir(path):
        if entry.is_dir():
            for sub_entry in os.scandir(entry.path):
                if sub_entry.is_file() and sub_entry.name in to_keep:
                    if verbose: print(f"Found {sub_entry.name} in {repo} first level, so keeping it and returning")
                    return os.path.join(path, entry.name, sub_entry.name)
        
    return None

def has_tests(path: str, build_file: str) -> bool:
    with open(build_file, "r") as f:
        content = f.read()
        if any(lib in content for lib in ["junit", "testng", "mockito"]):
            return True
        if any(keyword in content for keyword in ["testImplementation", "functionalTests", "bwc_tests_enabled"]):
            return True
    test_dirs = [
        "src/test/java",
        "src/test/kotlin",
        "src/test/groovy",
        "test",
    ]
    if any(os.path.exists(os.path.join(path, td)) for td in test_dirs):
        return True

    return False


def clone_repos(file: str, dest: str, force: bool =False, verbose: bool = False) -> None:
    """
    Download the repos listed in the file passed as argument. The downloaded repos will be placed in the folder that is named as the dest argument.


    Arguments:
        file (str): The name of the file to download the repos from. Must be a .csv.gz file (downloaded from https://seart-ghs.si.usi.ch)
        dest (str): The name of the root directory in which to download the repos
        verbose (bool): If `True`, outputs detailed process information. Defaults to `False`.
    """
    pandarallel.initialize(nb_workers=min(50, multiprocessing.cpu_count()-1), progress_bar=True, verbose=2 if verbose else 0)

    if verbose: print(f"Reading CSV file {file}")
    df = pd.read_csv(file)

    if verbose: print("Cloning repositories")
    def _process(repo: str)->None:
        if repo in EXCLUSION_LIST:
            print(f"Skipping {repo}, in exclusion list")
            return

        if force:
            clone(repo, dest, verbose=verbose)

        repo_path = os.path.join(dest, repo)
        if not os.path.exists(repo_path):
            return

        build_file = get_build_file(dest, repo)
        if build_file is None:
            print(f"Removing {repo}, no build file")
            shutil.rmtree(os.path.join(dest, repo))
            parent = os.path.abspath(os.path.join(dest, repo, os.path.pardir))
            if os.listdir(parent) == []:
                print(f"Removing {parent}, no files left")
                shutil.rmtree(parent)
            return
        
        if not has_tests(repo_path, build_file):
            print(f"Removing {repo}, no test suites")
            shutil.rmtree(os.path.join(dest, repo))
            return
        # if verbose: print(f"Keeping {repo}")

    df.name.progress_apply(_process)


if __name__ == "__main__":
    # whtie the code to parse the arguments here
    parser = argparse.ArgumentParser(description="Clone repos from a given file")
    parser.add_argument("file", default="results.csv.gz", help="The file to download the repos from. Default is 'results.csv.gz'")
    parser.add_argument("-d", "--dest", default="./results/", help="The root directory in which to download the repos. Default is './results/'")
    parser.add_argument("-f", "--force", action="store_true", help="Force the download of the repos")
    parser.add_argument("-v", "--verbose", action="store_true", help="Make the program verbose")
    args = parser.parse_args()

    clone_repos(args.file, args.dest, force=args.force, verbose=args.verbose)

