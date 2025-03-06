import os
from datetime import datetime
import pandas as pd
import tqdm
from github import Github

# Initialize GitHub API client
g = Github(os.environ["GITHUB_AUTH_TOKEN_CRAB"])

def has_only_1_round_of_comments(commits, comments):
    if not comments or not commits:
        return False
    
    commit_dates = [c.commit.author.date for c in commits]
    comment_dates = [c.created_at for c in comments]
    
    commit_dates.sort()
    comment_dates.sort()
    
    first_comment_time = comment_dates[0]
    last_comment_time = comment_dates[-1]
    
    for commit_time in commit_dates:
        if first_comment_time < commit_time < last_comment_time:
            return False
    
    return True

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
