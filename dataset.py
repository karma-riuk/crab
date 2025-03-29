from dataclasses import dataclass, field
from typing import Dict, List
import json, os
from github import Github
from collections import defaultdict
from github.PullRequest import PullRequest
from github.Repository import Repository
from tqdm import tqdm

from github.ContentFile import ContentFile

from utils import move_github_logging_to_file, run_git_cmd


# fmt: off
@dataclass
class FileData:
    path: str
    content: str = ""   # Not sure about this, maybe we should just keep the path and extract the contents dynamically (boh)

@dataclass
class FileData_new:
    is_code_related: bool
    coverage: Dict[str, float] # jacoco-report -> coverage
    content_before_pr: str = ""
    content_after_pr: str = ""

@dataclass
class Comment:
    body: str
    file: str
    from_: int
    to: int

@dataclass
class Metadata:
    repo: str   # the name of the repo, with style XXX/YYY
    pr_number: int
    merge_commit_sha: str   # to checkout for the tests
    commented_files: Dict[str, str]   # comment -> filename
    commented_files_coverages: Dict[str, Dict[str, float]] = field(default_factory=lambda: defaultdict(dict))     # filename -> jacoco-report -> coverage
    successful: bool = True
    build_system: str = ""
    reason_for_failure: str = ""
    last_cmd_error_msg: str = ""

@dataclass
class Metadata_new:
    repo: str   # the name of the repo, with style XXX/YYY
    pr_number: int
    pr_title: str
    pr_body: str
    merge_commit_sha: str   # to checkout for the tests
    successful: bool = True
    build_system: str = ""
    reason_for_failure: str = ""
    last_cmd_error_msg: str = ""


@dataclass
class DatasetEntry:
    metadata: Metadata
    files: Dict[str, FileData]   # filename -> file data, files before the PR (before the first PR commits)
    diffs_before: Dict[str, str]   # filename -> diff, diffs between the opening of the PR and the comment
    comments: List[str]
    diffs_after: Dict[str, str]   # filename -> diff, changes after the comment

@dataclass
class DatasetEntry_new:
    metadata: Metadata_new
    files: Dict[str, FileData_new]   # filename -> file data, files before the PR (before the first PR commits)
    diffs_before: Dict[str, str]   # filename -> diff, diffs between the opening of the PR and the comment
    comments: List[Comment]
    diffs_after: Dict[str, str]   # filename -> diff, changes after the comment


# fmt: on
@dataclass
class Dataset:
    entries: List[DatasetEntry] = field(default_factory=list)

    def __len__(self) -> int:
        return sum(1 for entry in self.entries if entry.metadata.successful)

    def to_json(self, filename: str):
        """Serialize the dataset to a JSON file"""
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self, f, default=lambda o: o.__dict__, indent=4)

    @staticmethod
    def from_json(filename: str, keep_still_in_progress: bool = False) -> "Dataset":
        with open(filename) as f:
            print(f"Loading dataset from {filename}...", end="")
            data = json.load(f)
            print("Done")

        entries = []
        for entry_data in tqdm(data["entries"], desc="Loading entries"):
            metadata_data = entry_data["metadata"]
            metadata = Metadata(**metadata_data)
            if (
                not keep_still_in_progress
                and metadata.reason_for_failure == "Was still being processed"
            ):
                continue

            files = {fname: FileData(**fdata) for fname, fdata in entry_data["files"].items()}

            entry = DatasetEntry(
                metadata=metadata,
                files=files,
                diffs_before=entry_data["diffs_before"],
                comments=entry_data["comments"],
                diffs_after=entry_data["diffs_after"],
            )
            entries.append(entry)

        return Dataset(entries=entries)


@dataclass
class Dataset_new:
    entries: List[DatasetEntry_new] = field(default_factory=list)

    def __len__(self) -> int:
        return sum(1 for entry in self.entries if entry.metadata.successful)

    def to_json(self, filename: str):
        """Serialize the dataset to a JSON file"""
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self, f, default=lambda o: o.__dict__, indent=4)

    @staticmethod
    def from_json(filename: str, keep_still_in_progress: bool = False) -> "Dataset":
        raise NotImplementedError("This method is not implemented yet")


def migrate(dataset: Dataset) -> Dataset_new:
    ret = Dataset_new()
    for entry in tqdm(dataset.entries, desc="Migrating entries"):
        new_entry = new_entry_form_old(entry)
        ret.entries.append(new_entry)
    return ret

def try_decode(content: bytes) -> str:
    try:
        return content.decode()
    except UnicodeDecodeError:
        return "Binary file (from API), to be ignored"

def try_read_file(fname: str) -> str:
    if not os.path.exists(fname):
         return "" # file was removed after the PR
    try:
        with open(fname, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Binary file (from filesystem), to be ignored"

def new_files(repo: Repository, pr: PullRequest, new_metadata: Metadata_new, old_entry: DatasetEntry, repo_path: str) -> dict[str, FileData_new]:
    review_comments = list(pr.get_review_comments())
    if not review_comments:
        raise ValueError(
            f"No review comments found for PR #{new_metadata.pr_number} in {new_metadata.repo}"
        )

    assert (
        len(review_comments) == 1
    ), f"Multiple review comments found for PR #{new_metadata.pr_number} in {new_metadata.repo}"
    comment_commit_id = review_comments[0].original_commit_id

    ret = {}
    for fname in old_entry.files:
        try:
            contents = repo.get_contents(fname, ref=comment_commit_id)
            assert isinstance(
                contents, ContentFile
            ), f"Multiple files with the same name {fname} in base sha {comment_commit_id} ({contents})"
            content_before = try_decode(contents.decoded_content)
        except Exception as e:
            content_before = ""   # file didn't exist before the PR

        if old_entry.metadata.reason_for_failure == "Couldn't fetch the PR's merge commit":
            content_after = ""
        else:
            try:
                contents = repo.get_contents(fname, ref=pr.merge_commit_sha)
                assert isinstance(
                    contents, ContentFile
                ), f"Multiple files with the same name {fname} in base sha {comment_commit_id} ({contents})"
                content_after = try_decode(contents.decoded_content)

            except Exception as e:
                run_git_cmd(["checkout", pr.merge_commit_sha], repo_path)
                content_after = try_read_file(os.path.join(repo_path, fname))

        ret[fname] = FileData_new(
            is_code_related=fname.endswith('.java'),
            coverage=old_entry.metadata.commented_files_coverages.get(fname, {}),
            content_before_pr=content_before,
            content_after_pr=content_after,
        )
    return ret

def new_comments(pr: PullRequest, new_metadata: Metadata_new) -> list[Comment]:
    review_comments = list(pr.get_review_comments())
    ret = [
        Comment(
            body=comment.body,
            file=comment.path,
            from_=comment.start_line if comment.start_line else comment.line,
            to=comment.line,
        )
        for comment in review_comments
    ]
    if ret[0].from_ is None or ret[0].to is None:
        ret[0].to = review_comments[0].original_line
        ret[0].from_ = review_comments[0].original_start_line
        if ret[0].from_ is None:
            ret[0].from_ = review_comments[0].original_line

        # if ret[0].from_ is None or ret[0].to is None:
        #     print(
        #         f"PR #{new_metadata.pr_number} in {new_metadata.repo} has a comment without line numbers"
        #     )
    return ret


def new_entry_form_old(entry: DatasetEntry) -> DatasetEntry_new:
    with tqdm(total=3, desc="Migrating entry", leave=False) as pbar:
        pbar.set_postfix_str(f"Extracting metadata")
        new_metadata = new_metadata_from_old(entry.metadata)
        pbar.update(1)
        repo = g.get_repo(new_metadata.repo)
        pr = repo.get_pull(new_metadata.pr_number)

        pbar.set_postfix_str(f"Extracting files")
        new_files_ = new_files(repo, pr, new_metadata, entry, os.path.join("results", new_metadata.repo))
        pbar.update(1)
        pbar.set_postfix_str(f"Extracting comments")
        new_comments_ = new_comments(pr, new_metadata)
        pbar.update(1)

        return DatasetEntry_new(
            metadata=new_metadata,
            files=new_files_,
            diffs_before=entry.diffs_before,
            comments=new_comments_,
            diffs_after=entry.diffs_after,
        )


def new_metadata_from_old(metadata: Metadata) -> Metadata_new:
    repo = g.get_repo(metadata.repo)
    pr = repo.get_pull(metadata.pr_number)
    return Metadata_new(
        repo=metadata.repo,
        pr_number=metadata.pr_number,
        pr_title=pr.title,
        pr_body=pr.body,
        merge_commit_sha=metadata.merge_commit_sha,
        successful=metadata.successful,
        build_system=metadata.build_system,
        reason_for_failure=metadata.reason_for_failure,
        last_cmd_error_msg=metadata.last_cmd_error_msg,
    )


if __name__ == "__main__":
    g = Github(os.environ["GITHUB_AUTH_TOKEN_CRAB"])

    dataset = Dataset.from_json("dataset.json")
    new_dataset = migrate(dataset)
    print("done, saving...")
    new_dataset.to_json("dataset.new.json")
