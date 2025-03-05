import os, requests, json
from datetime import datetime

COMMON_HEADERS = {
    'Accept': 'application/vnd.github+json',
    'Authorization': f'Bearer {os.environ["GITHUB_AUTH_TOKEN_CRAB"]}',
    'X-Github-Api-Version': '2022-11-28',
}

def github_call(url):
    return requests.get(url, headers=COMMON_HEADERS)

def get_comments(repo_url: str, pr_number: str) -> list[dict]:
    response = github_call(f'{repo_url}/pulls/{pr_number}/comments')
    return response.json()

def get_commits(repo_url: str, pr_number: str) -> list[dict]:
    response = github_call(f'{repo_url}/pulls/{pr_number}/commits')
    return response.json()

def extract_date(date: str) -> datetime:
    return datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")

def get_first_comment_date(comments: list[dict]) -> datetime:
    return min([extract_date(comment['created_at']) for comment in comments])

def get_useful_commits(commits: list[dict], first_comment_date: datetime) -> list[dict]:
    ret = []
    for commit in commits:
        if ("commit" not in commit 
                and "author" not in commit["author"] 
                and "date" not in commit['commit']['author']):
            continue
        commit_date = extract_date(commit['commit']['author']['date'])
        if commit_date > first_comment_date:
            ret.append(commit)
    return ret


def process_pull_request(repo_url: str, pr_number: str) -> bool:
    comments = get_comments(repo_url, pr_number)

    if len(comments) == 0:
        # No comments, can't extract triplet
        return False


    first_comment_date = get_first_comment_date(comments)
    commits = get_commits(repo_url, pr_number)

    if len(commits) == 0:
        # No commits, can't extract triplet
        return False

    # filter out the commits that are older than the first comment, since they are the commits relevant for the PR
    actual_commits = get_useful_commits(commits, first_comment_date)

    if len(actual_commits) == 0:
        # No commits after the first comment, there were no revision from the contributor, so no triplet
        return False


    for commit in actual_commits:
        print(f"Commit: {commit['sha']}")
        print(f"Author: {commit['author']['login']}")
        print(f"Date: {commit['commit']['author']['date']}")
        print(f"Message: {commit['commit']['message']}")
        print("")

    return True




if __name__ == "__main__":
    response  = github_call('https://api.github.com/repos/cdk/cdk/pulls/1140/commits')
    response  = github_call('https://api.github.com/repos/cdk/cdk/pulls/1140/commits')
    process_pull_request('https://api.github.com/repos/cdk/cdk', '1140')
