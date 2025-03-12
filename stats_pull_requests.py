import os, logging
from datetime import datetime
import pandas as pd
from tqdm import tqdm
from github import Github

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

def parse_date(date: str) -> datetime:
    return datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")

def has_only_1_round_of_comments(commits, comments):
    if not comments or not commits:
        return False
    
    commit_dates = []
    for commit in commits:
        if isinstance(commit.commit.author.date, str):
            commit_dates.append(parse_date(commit.commit.author.date))
        elif isinstance(commit.commit.author.date, datetime):
            commit_dates.append(commit.commit.author.date)
        else:
            logging.warning(f"The commit {commit.sha} has an unexpected date format: {commit.commit.author.date}")
            logging.warning(f"Tied to PR: {comments[0]['pull_request_url']}")
            return False

    comment_dates = []
    for comment in comments:
        if isinstance(comment.created_at, str):
            comment_dates.append(parse_date(comment.created_at))
        elif isinstance(comment.created_at, datetime):
            comment_dates.append(comment.created_at)
        else:
            logging.warning(f"The comment {comment['id']} has an unexpected date format: {comment['created_at']}")
            logging.warning(f"Tied to PR: {comment['pull_request_url']}")
            return False
    
    commit_dates.sort()
    comment_dates.sort()
    
    first_comment_time = comment_dates[0]
    last_comment_time = comment_dates[-1]
    
    n_before = n_after = 0
    for commit_time in tqdm(commit_dates, desc="Checking for 1 round of comments", leave=False):
        if commit_time < first_comment_time:
            n_before += 1
            continue
        if commit_time > last_comment_time:
            n_after += 1
            continue

        if first_comment_time < commit_time < last_comment_time:
            return False
    
    return n_before >= 1 and n_after >= 1

def has_only_1_comment(commits, comments):
    if not comments or not commits:
        return False

    commit_dates = []
    for commit in commits:
        if isinstance(commit.commit.author.date, str):
            commit_dates.append(parse_date(commit.commit.author.date))
        elif isinstance(commit.commit.author.date, datetime):
            commit_dates.append(commit.commit.author.date)
        else:
            logging.warning(f"The commit {commit.sha} has an unexpected date format: {commit.commit.author.date}")
            logging.warning(f"Tied to PR: {comments[0]['pull_request_url']}")
            return False
    commit_dates.sort()

    if isinstance(comments[0].created_at, datetime):
        comment_date = comments[0].created_at
    elif isinstance(comments[0].created_at, str):
        comment_date = parse_date(comments[0].created_at)
    else:
        logging.warning(f"The comment {comments[0]['id']} has an unexpected date format: {comments[0]['created_at']}")
        return False

    n_before = n_after = 0
    for commit_date in tqdm(commit_dates, desc="Checking for 1 comment", leave=False):
        if commit_date < comment_date:
            n_before += 1
            continue
        if commit_date > comment_date:
            n_after += 1
            continue
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
