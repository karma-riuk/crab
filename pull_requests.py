import os, requests, re
from datetime import datetime
from typing import Optional
import itertools
import pandas as pd

from unidiff import PatchSet
from io import StringIO

COMMON_HEADERS = {
    'Accept': 'application/vnd.github+json',
    'Authorization': f'Bearer {os.environ["GITHUB_AUTH_TOKEN_CRAB"]}',
    'X-Github-Api-Version': '2022-11-28',
}

def github_call(url):
    result = requests.get(url, headers=COMMON_HEADERS)
    if result.status_code != 200:
        raise Exception(f"Failed to fetch {url}: {result.status_code}")
    return result

def get_comments(repo_url: str, pr_number: str) -> list[dict]:
    response = github_call(f'{repo_url}/pulls/{pr_number}/comments')
    return response.json()

def get_commit(repo_url: str, commit_sha: str) -> dict:
    response = github_call(f'{repo_url}/commits/{commit_sha}')
    return response.json()

def get_commits(repo_url: str, pr_number: str) -> list[dict]:
    response = github_call(f'{repo_url}/pulls/{pr_number}/commits')
    commits = response.json()
    for commit in commits:
        detailed_commit = get_commit(repo_url, commit['sha'])
        if "files" not in detailed_commit:
            continue

        for file in detailed_commit['files']:
            file["patch_range"] = parse_hunk_header(file['patch'])
        commit["files"] = detailed_commit["files"]
    return commits

def parse_date(date: str) -> datetime:
    return datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")

def get_first_comment_date(comments: list[dict]) -> datetime:
    return min([parse_date(comment['created_at']) for comment in comments])

def get_useful_commits(commits: list[dict], first_comment_date: datetime) -> list[dict]:
    ret = []
    for commit in commits:
        if ("commit" not in commit 
                and "author" not in commit["author"] 
                and "date" not in commit['commit']['author']):
            continue
        commit_date = parse_date(commit['commit']['author']['date'])
        if commit_date > first_comment_date:
            ret.append(commit)
    return ret

def parse_hunk_header(hunk_header) -> Optional[dict]:
    """Extracts line ranges from a diff hunk header."""
    match = re.match(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@', hunk_header)
    if match:
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) else 1
        return {
            "old_range": {
                "start" : old_start,
                "end" : old_start + old_count - 1
            },
            "new_range": {
                "start" : new_start,
                "end" : new_start + new_count - 1
            },
        }
    return None

def augment_comments(comments: list[dict]) -> list[dict]:
    ret = []
    for comment in comments:
        new_comment = comment.copy()
        if "diff_hunk" not in comment:
            continue
        new_comment["hunk_range"] = parse_hunk_header(comment["diff_hunk"])
        ret.append(new_comment)
    return ret

def is_range_overlapping(range1: dict, range2: dict) -> bool:
    return range1["start"] <= range2["start"] <= range1["end"] or range2["start"] <= range1["start"] <= range2["end"]

def get_overlapping_commits_and_comments(commits: list[dict], comments: list[dict]) -> list[tuple[dict, dict]]:
    ret = []
    for commit, comment in itertools.product(commits, comments):
        if "hunk_range" not in comment:
            continue
        if "files" not in commit:
            continue
        if parse_date(commit['commit']['author']['date']) < parse_date(comment['created_at']):
            # we can't address a comment if that comment was made after the commit
            continue
        for file in commit["files"]:
            if "patch_range" not in file:
                continue
            if file["filename"] == comment["path"]:
                if is_range_overlapping(file["patch_range"]["old_range"], comment["hunk_range"]["new_range"]):
                    commit_copy = commit.copy()
                    commit_copy["relevant_file"] = file
                    ret.append((commit_copy, comment))
    return ret

def reverse_patch(file_after: str, patch_content: str) -> str:
    """
    Reverses a patch and applies it to a file to get the version of the file before the patch.
    """
    # Parse the patch
    patch = PatchSet(StringIO(patch_content))
    
    # Extract the file to be patched
    after_lines = file_after.splitlines(keepends=True)

    for patched_file in patch:
        if patched_file.is_modified_file:
            original_lines = after_lines[:]
            modified_lines = []

            # Apply the patch in reverse
            for hunk in patched_file:
                hunk_lines = [str(line.value) for line in hunk.source_lines()]
                new_start = hunk.target_start - 1
                new_end = new_start + hunk.target_length

                # Replace modified section with original content from patch
                modified_lines.extend(original_lines[:new_start])
                modified_lines.extend(hunk_lines)
                original_lines = original_lines[new_end:]

            modified_lines.extend(original_lines)
            return "".join(modified_lines)

    return file_after  # Return unmodified if no patch applies

def extract_triplet(commit_comments: list[tuple[dict, dict]])-> list[dict]:
    ret = []
    for commit, comment in commit_comments:
        file_after = github_call(commit["relevant_file"]["raw_url"]).text
        filename = comment["path"]
        patch_content = f"--- a/{filename}\n+++ b/{filename}\n" + commit["relevant_file"]["patch"] + "\n"
        file_before = reverse_patch(file_after, patch_content)
        ret.append({
            "file_before": file_before, 
            "comment": comment["body"], 
            "file_after": file_after
        })
    return ret

def process_pull_request(repo_url: str, pr_number: str) -> bool:
    tmp_comments = get_comments(repo_url, pr_number)
    comments = augment_comments(tmp_comments)

    if len(comments) == 0:
        # No comments, can't extract triplet
        return False

    first_comment_date = get_first_comment_date(comments)

    # get commits and filter out the ones that are older than the first
    # comment, since they are the commits relevant for the PR
    tmp_commits = get_commits(repo_url, pr_number)
    commits = get_useful_commits(tmp_commits, first_comment_date)

    if len(commits) == 0:
        # No commits after the first comment, there were no revision from the contributor, so no triplet
        return False

    overlapping_commits_and_comments = get_overlapping_commits_and_comments(commits, comments)

    triplets_df = pd.DataFrame(extract_triplet(overlapping_commits_and_comments))
    repo_name = "/".join(repo_url.split("/")[-2:])
    triplets_df["repo"] = repo_name
    triplets_df["pr_number"] = pr_number
    triplets_df.to_csv("triplets.csv", index=False)

    return True

def is_pr_eligible(pr: dict) -> bool:
    return pr['state'] == 'closed' and pr['merged_at'] is not None

def process_repo(repo_name: str) -> None:
    all_triplets = pd.DataFrame()
    prs = github_call(f'https://api.github.com/repos/{repo_name}/pulls?state=closed').json()
    for pr in prs:
        if not is_pr_eligible(pr):
            continue
        triplets = process_pull_request(f'https://api.github.com/repos/{repo_name}', str(pr['number']))
        all_triplets = all_triplets.append(triplets, ignore_index=True)

    all_triplets.to_csv("triplets.csv", index=False)

if __name__ == "__main__":
    process_pull_request('https://api.github.com/repos/cdk/cdk', '1140')
