from dataclasses import dataclass, field
from typing import Dict, List
import json

# fmt: off
@dataclass
class FileData:
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
        with open(filename, "r", encoding="utf-8") as f:
            print(f"Loading dataset from {filename}...", end="")
            data = json.load(f)
            print("Done")

        entries = []
        for entry_data in data["entries"]:
            metadata_data = entry_data["metadata"]
            metadata = Metadata(**metadata_data)

            if (
                not keep_still_in_progress
                and metadata.reason_for_failure == "Was still being processed"
            ):
                continue

            files = {fname: FileData(**fdata) for fname, fdata in entry_data["files"].items()}

            comments = [Comment(**comment) for comment in entry_data["comments"]]

            entry = DatasetEntry(
                metadata=metadata,
                files=files,
                diffs_before=entry_data["diffs_before"],
                comments=comments,
                diffs_after=entry_data["diffs_after"],
            )
            entries.append(entry)

        return Dataset(entries=entries)
