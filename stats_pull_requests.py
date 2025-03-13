import os, logging
from datetime import datetime
import pandas as pd
from tqdm import tqdm
from github import Github
from utils import has_only_1_round_of_comments, has_only_1_comment

tqdm.pandas()

# Initialize GitHub API client
g = Github(os.environ["GITHUB_AUTH_TOKEN_CRAB"])

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

def process_pull(repo, pull):
    commits = list(pull.get_commits())
    comments = list(pull.get_review_comments())
    
    return {
        "repo": repo.full_name,
        "pr_number": pull.number,
        "additions": pull.additions,
        "deletions": pull.deletions,
        "changed_files": pull.changed_files,
        "has_only_1_round_of_comments": has_only_1_round_of_comments(commits, comments),
        "has_only_1_comment": has_only_1_comment(commits, comments),
    }

def process_repo(repo_name):
    repo = g.get_repo(repo_name)
    stats = []
    
    with tqdm(list(repo.get_pulls(state="closed")), desc=repo_name, leave=False) as pbar:
        for pull in pbar:
            pbar.set_postfix({"started at": datetime.now().strftime("%d/%m, %H:%M:%S")})
            if not pull.merged_at:
                continue
        
            stats.append(process_pull(repo, pull))
    return stats

def main():
    repos = pd.read_csv("results.csv")
    repos = repos[(repos["good_repo_for_crab"] == True) & (repos["n_tests"] > 0)]
    stats = []
    
    try:
        for _, row in tqdm(repos.iterrows(), total=len(repos)):
            if "name" not in row or not isinstance(row["name"], str):
                continue
            stats.extend(process_repo(row["name"]))
            pd.DataFrame(stats).to_csv("pr_stats.csv", index=False)
    finally:
        pd.DataFrame(stats).to_csv("pr_stats.csv", index=False)

if __name__ == "__main__":
    move_github_logging_to_file()
    main()
