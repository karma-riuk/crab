from dataclasses import dataclass, field
from typing import List
import json

@dataclass
class FileData:
    path: str
    content: str = "" # Not sure about this, maybe we should just keep the path and extract the contents dynamically (boh)

@dataclass
class Metadata:
    repo: str # the name of the repo, with style XXX/YYY 
    pr_number: int
    merge_commit_sha: str # to checkout for the tests
    successful: bool
    reason_for_failure: str = ""
    last_cmd_error_msg: str = ""

@dataclass
class Diff:
    filename: str
    patch: str

@dataclass
class DatasetEntry:
    metadata: Metadata
    files: List[FileData] # files before the PR (before the first PR commits)
    diffs_before: List[Diff] # diffs between the opening of the PR and the comment
    comment: str
    diffs_after: List[Diff] # changes after the comment

@dataclass
class Dataset:
    entries: List = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.entries)

    def to_json(self, filename: str):
        """Serialize the dataset to a JSON file"""
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self, f, default=lambda o: o.__dict__, indent=4)

    @staticmethod
    def from_json(filename: str):
        """Load the dataset from a JSON file"""
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
            return Dataset(
                entries=[
                    DatasetEntry(
                        metadata=Metadata(**entry["metadata"]),
                        files=[FileData(**file) for file in entry["files"]],
                        diffs_before=entry["diffs_before"],
                        comment=entry["comment"],
                        diffs_after=entry["diffs_after"]
                    ) for entry in data["entries"]
                ]
            )
