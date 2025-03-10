import os, logging
from datetime import datetime
import pandas as pd
import tqdm
from github import Github

# Set up logging
log_file = "github_api.log"
logging.basicConfig(
    filename=log_file,
    level=logging.WARNING,  # Adjust as needed
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Redirect PyGithub logging
logging.getLogger("github.Requester").setLevel(logging.WARNING)

# Initialize GitHub API client
g = Github(os.environ["GITHUB_AUTH_TOKEN_CRAB"])

def parse_date(date: str) -> datetime:
    return datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")

def has_only_1_round_of_comments(commits, comments):
    if not comments or not commits:
        return False
    
    commit_dates = [parse_date(c.commit.author.date) for c in commits]
    comment_dates = [parse_date(c.created_at) for c in comments]
    
    commit_dates.sort()
    comment_dates.sort()
    
    first_comment_time = comment_dates[0]
    last_comment_time = comment_dates[-1]
    
    n_before = n_after = 0
    for commit_time in commit_dates:
        if commit_time < first_comment_time:
            n_before += 1
            continue
        if commit_time > last_comment_time:
            n_after += 1
            continue

        if first_comment_time < commit_time < last_comment_time:
            return False
    
    return n_before >= 1 and n_after >= 1

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
        "has_only_1_comment": len(comments) == 1,
    }

def process_repo(repo_name):
    repo = g.get_repo(repo_name)
    stats = []
    
    for pull in tqdm.tqdm(list(repo.get_pulls(state="closed")), desc=repo_name, leave=False):
        if not pull.merged_at:
            continue
        
        stats.append(process_pull(repo, pull))
    return stats

def main():
    repos = pd.read_csv("results.csv")
    repos = repos[repos["good_repo_for_crab"] == True]
    stats = []
    
    try:
        for _, row in tqdm.tqdm(repos.iterrows(), total=len(repos)):
            if "name" not in row or not isinstance(row["name"], str):
                continue
            stats.extend(process_repo(row["name"]))
    finally:
        pd.DataFrame(stats).to_csv("pr_stats.csv", index=False)

if __name__ == "__main__":
    main()
