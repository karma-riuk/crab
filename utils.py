from datetime import datetime
from github.Commit import Commit
from github.PaginatedList import PaginatedList
from github.PullRequestComment import PullRequestComment
from tqdm import tqdm

def parse_date(date: str) -> datetime:
    return datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")

def has_only_1_round_of_comments(commits: PaginatedList[Commit], comments: PaginatedList[PullRequestComment]):
    if (
        comments is None or commits is None 
        or comments.totalCount == 0 or commits.totalCount == 0
    ):
        return False
    
    commit_dates = [commit.commit.author.date for commit in tqdm(commits, total=commits.totalCount, desc="Extracting date from commits", leave=False)]
    comment_dates = [comment.created_at for comment in tqdm(comments, total=comments.totalCount, desc="Extracting date from comments", leave=False)]
    
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

def has_only_1_comment(commits: PaginatedList[Commit], comments: PaginatedList[PullRequestComment], verbose: bool = False):
    if (
        comments is None or commits is None 
        or comments.totalCount == 0 or commits.totalCount == 0
    ):
        if verbose: print(f"No comments or commits: {comments.totalCount} comments, {commits.totalCount} commits")
        return False

    if comments.totalCount != 1:
        if verbose: print(f"More than 1 comment: {comments.totalCount} comments")
        return False

    commit_dates = [commit.commit.author.date for commit in commits]

    comment_date = comments[0].created_at

    n_before = n_after = 0
    for commit_date in commit_dates:
        if commit_date < comment_date:
            n_before += 1
            continue
        if commit_date > comment_date:
            n_after += 1
            continue
    if verbose: print(f"n_before: {n_before}, n_after: {n_after}")
    return n_before >= 1 and n_after >= 1
