from dataclasses import dataclass
from enum import Enum
import os, json, tarfile, argparse
from typing import Optional
from dataset import Dataset, ArchiveState
from utils import EnumChoicesAction


class OutputType(Enum):
    CODE_REFINEMENT = "code_refinement"
    COMMENT_GEN = "comment_gen"
    FOR_PARAPHRASES = "paraphrases"


@dataclass
class CommentGenSubmission:
    path: str
    line_from: int
    line_to: Optional[int]
    body: str


def extract_comment_for_paraphrases(dataset_path: str, output_path: str):
    dataset = Dataset.from_json(dataset_path)
    results: dict[str, dict] = {}

    for entry in dataset.entries:
        sel = entry.metadata.selection
        if sel and sel.comment_suggests_change:
            comment = entry.comments[0].__dict__
            del comment["paraphrases"]
            results[entry.metadata.id] = {
                "link": f"https://github.com/{entry.metadata.repo}/pull/{entry.metadata.pr_number}",
                "comment": comment,
                "files": {fname: fdata.content_before_pr for fname, fdata in entry.files.items()},
                "diffs_before": entry.diffs_before,
            }

    # Write out the exact predictions reference JSON
    with open(output_path, "w", encoding="utf-8") as out_file:
        json.dump(results, out_file, default=lambda o: o.__dict__, indent=4)

    print(f"Saved {len(results)} entries to {output_path}")


def extract_comment_predictions(dataset_path: str, output_path: str):
    dataset = Dataset.from_json(dataset_path)
    results: dict[str, CommentGenSubmission] = {}
    for entry in dataset.entries:
        sel = entry.metadata.selection
        if sel and sel.comment_suggests_change:
            results[entry.metadata.id] = CommentGenSubmission(
                path=entry.comments[0].file,
                line_from=entry.comments[0].from_,
                line_to=entry.comments[0].to,
                body=entry.comments[0].body,
            )

    # Write out the exact predictions reference JSON
    with open(output_path, "w", encoding="utf-8") as out_file:
        json.dump(results, out_file, default=lambda o: o.__dict__, indent=4)

    print(f"Saved {len(results)} entries to {output_path}")


def extract_refinement_predictions(dataset_path: str, archives_path: str, output_path: str):
    # Load the dataset
    dataset = Dataset.from_json(dataset_path)
    results = {}

    # Iterate over entries that address the change
    for entry in dataset.entries:
        sel = entry.metadata.selection
        if not sel or not (sel.diff_after_address_change and sel.is_code_related):
            continue
        entry_id = entry.metadata.id

        # Determine the merged archive filename
        archive_filename = entry.metadata.archive_name(ArchiveState.MERGED)
        archive_path = os.path.join(archives_path, archive_filename)
        if not os.path.exists(archive_path):
            print(f"Archive not found: {archive_path}")
            continue

        # Extract file contents after merge
        with tarfile.open(archive_path, "r:gz") as tar:
            file_contents = {}
            for filename in entry.diffs_after.keys():
                # Find the member matching the file path
                member = next((m for m in tar.getmembers() if m.name.endswith(filename)), None)
                if member is None:
                    print(f"File {filename} not found in {archive_path}")
                    continue
                f = tar.extractfile(member)
                if f is None:
                    print(f"Could not extract {filename} from {archive_path}")
                    continue
                content = f.read().decode("utf-8", errors="replace")
                file_contents[filename] = content

        results[entry_id] = file_contents

    # Write out the exact predictions reference JSON
    with open(output_path, "w", encoding="utf-8") as out_file:
        json.dump(results, out_file, indent=4)

    print(f"Saved {len(results)} entries to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract merged archive contents for entries addressing a change"
    )
    parser.add_argument(
        'dataset',
        help='Path to the dataset JSON file',
    )
    parser.add_argument(
        '-o',
        '--output',
        help='Path to the output JSON file. Default is `exact_predictions_{output-type}.json`',
    )
    parser.add_argument(
        '-a',
        '--archives',
        help='Directory where archive files are located. Required if output type is code_refinement',
    )
    parser.add_argument(
        "-t",
        "--output-type",
        type=OutputType,
        default=OutputType.COMMENT_GEN,
        action=EnumChoicesAction,
        help="Type of output to generate",
    )
    args = parser.parse_args()

    output_type = OutputType(args.output_type)
    if args.output is None:
        args.output = f"exact_predictions_{output_type.value}.json"

    if output_type is OutputType.COMMENT_GEN:
        extract_comment_predictions(args.dataset, args.output)
    elif output_type is OutputType.CODE_REFINEMENT:
        assert args.archives is not None
        extract_refinement_predictions(args.dataset, args.archives, args.output)
    elif output_type is OutputType.FOR_PARAPHRASES:
        extract_comment_for_paraphrases(args.dataset, args.output)
