from datetime import datetime
import logging

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
