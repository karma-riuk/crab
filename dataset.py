from dataclasses import dataclass, field
from typing import Dict, List
import json

from collections import defaultdict


# fmt: off
@dataclass
class FileData:
    path: str
    content: str = ""   # Not sure about this, maybe we should just keep the path and extract the contents dynamically (boh)


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
class DatasetEntry:
    metadata: Metadata
    files: Dict[str, FileData]   # filename -> file data, files before the PR (before the first PR commits)
    diffs_before: Dict[str, str]   # filename -> diff, diffs between the opening of the PR and the comment
    comments: List[str]
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
            data = json.load(f)

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

            entry = DatasetEntry(
                metadata=metadata,
                files=files,
                diffs_before=entry_data["diffs_before"],
                comments=entry_data["comments"],
                diffs_after=entry_data["diffs_after"],
            )
            entries.append(entry)

        return Dataset(entries=entries)
