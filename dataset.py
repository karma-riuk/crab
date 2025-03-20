from dataclasses import dataclass, field
from typing import Dict, List
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
    commented_file : str
    commented_file_coverage: float = 0
    successful: bool = True
    build_system: str = ""
    reason_for_failure: str = ""
    last_cmd_error_msg: str = ""

@dataclass
class DatasetEntry:
    metadata: Metadata
    files: Dict[str, FileData] # filename -> file data, files before the PR (before the first PR commits)
    diffs_before: Dict[str, str] # filename -> diff, diffs between the opening of the PR and the comment
    comment: str
    diffs_after: Dict[str, str] # filename -> diff, changes after the comment

@dataclass
class Dataset:
    entries: List[DatasetEntry] = field(default_factory=list)

    def __len__(self) -> int:
        return sum(1 for entry in self.entries if entry.metadata.successful)

    def to_json(self, filename: str):
        """Serialize the dataset to a JSON file"""
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self, f, default=lambda o: o.__dict__, indent=4)
