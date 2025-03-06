import os, requests
from datetime import datetime
import pandas as pd
import tqdm

COMMON_HEADERS = {
    'Accept': 'application/vnd.github+json',
    'Authorization': f'Bearer {os.environ["GITHUB_AUTH_TOKEN_CRAB"]}',
    'X-Github-Api-Version': '2022-11-28',
}

def github_call(url, params = {}):
    result = requests.get(url, headers=COMMON_HEADERS, params=params)
    if result.status_code != 200:
        raise Exception(f"Failed to fetch {url}: {result.status_code}, {result = }")
    return result

def get_pulls(repo_url: str) -> list[dict]:
    response = github_call(f'{repo_url}/pulls', params={"state": "all"})
    return response.json()

def has_only_1_round_of_comments(commits: list[dict], comments: list[dict]) -> bool:
    if len(comments) == 0 or len(commits) == 0:
        return False

    # Convert timestamps to datetime objects for easy comparison
    commit_dates = [datetime.fromisoformat(c["commit"]["author"]["date"]) for c in commits]
    comment_dates = [datetime.fromisoformat(c["created_at"]) for c in comments]
    commit_dates.sort()
    comment_dates.sort()

    # Identify the first and last comment times
    first_comment_time = comment_dates[0]
    last_comment_time = comment_dates[-1]

    for commit_time in commit_dates:
        if first_comment_time < commit_time < last_comment_time:
            return False

    return True


def process_pull(repo_name: str, pull_number: str) -> dict:
    pull = github_call(f"https://api.github.com/repos/{repo_name}/pulls/{pull_number}").json()
    commits = github_call(f"https://api.github.com/repos/{repo_name}/pulls/{pull_number}/commits").json()
    comments = github_call(f"https://api.github.com/repos/{repo_name}/pulls/{pull_number}/comments").json()

    return {
        "repo": repo_name,
        "pr_number": pull["number"],
        "additions": pull["additions"],
        "deletions": pull["deletions"],
        "changed_files": pull["changed_files"],
        "has_only_1_round_of_comments": has_only_1_round_of_comments(commits, comments),
        "has_only_1_comment": len(comments) == 1,
    }

def process_repo(repo_name: str) -> list[dict]:
    stats = []
    pulls = get_pulls(f"https://api.github.com/repos/{repo_name}")
    for pull in tqdm.tqdm(pulls, desc=repo_name, leave=False):
        if "merged_at" not in pull or pull["merged_at"] is None:
            continue

        stats.append(process_pull(repo_name, pull["number"]))
    return stats


def main():
    repos = pd.read_csv("results.csv")
    repos = repos[repos["good_repo_for_crab"] == True]
    print(len(repos))
    stats = []

    for _, row in tqdm.tqdm(repos.iterrows(), total=len(repos)):
        if "name" not in row or not isinstance(row["name"], str):
            continue
        name = row["name"]
        stats.extend(process_repo(name))

    pd.DataFrame(stats).to_csv("pr_stats.csv", index=False)


if __name__ == "__main__":
    main()
