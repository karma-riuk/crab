import os
from datetime import datetime
import pandas as pd
from tqdm import tqdm
from github import Github
from utils import has_only_1_round_of_comments, has_only_1_comment, move_logger_to_file

tqdm.pandas()

# Initialize GitHub API client
g = Github(os.environ["GITHUB_AUTH_TOKEN_CRAB"])


def process_pull(repo, pull):
    commits = pull.get_commits()
    comments = pull.get_review_comments()

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
    move_logger_to_file("github", "github_api.log")
    main()
