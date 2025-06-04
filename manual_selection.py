from typing import Optional
from dataset import Dataset, DatasetEntry, Selection
import argparse, os, re, click
from enum import Enum
from utils import EnumChoicesAction, prompt_yes_no

HUNK_HEADER_REGEX = re.compile(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@')


class ValidationMode(Enum):
    COMMENT = "comment"
    REFINEMENT = "refinement"


def green(line: str) -> str:
    return f"\033[32m{line}\033[0m"


def red(line: str) -> str:
    return f"\033[31m{line}\033[0m"


def bold(line: str) -> str:
    return f"\033[1m{line}\033[0m"


def pretty_diff(after: str) -> str:
    lines = after.splitlines()
    pretty_lines = []
    for line in lines:
        if line.startswith("+"):
            pretty_lines.append(green(line))
        elif line.startswith("-"):
            pretty_lines.append(red(line))
        elif line.startswith("@@"):
            pretty_lines.append(bold(line))
        else:
            pretty_lines.append(line)
    return "\n".join(pretty_lines)


def split_into_hunks(diff: str) -> list[str]:
    """
    Given a unified diff string, split it into chunks, each starting with a
    hunk header (“@@ -… +… @@”) and including all context lines for that hunk.
    """
    if not diff:
        return []
    # The regex will keep the “@@ … @@” lines as the start of each hunk.
    parts = re.split(r'(?m)(^@@ .*@@)', diff)
    # re.split returns something like ['', header1, body1, header2, body2, …]
    hunks = []
    for i in range(1, len(parts), 2):
        header = parts[i]
        body = parts[i + 1]
        hunks.append(header + body)
    return hunks


def edit_hunk(hunk: str) -> str:
    while True:
        edited = click.edit(hunk)
        if edited is None:
            print("Edit aborted, keeping original hunk")
            return hunk
        lines = edited.splitlines()
        # Validate that the hunk header remains intact
        if lines and HUNK_HEADER_REGEX.match(lines[0]):
            return edited
        else:
            print(red("Invalid hunk header! Hunk header must not be modified."))
            if not prompt_yes_no("Edit again?", default=False):
                print("Keeping original hunk")
                return hunk


def display_header(i: int, total: int, n_good: int):
    cols = os.get_terminal_size().columns
    print("#" * cols)
    print(f"# good PRs: {n_good}/{i} ({n_good / i:.2%})")
    print(f"Current PR: {i}/{total} ({i / total:.2%})")


def display_pr_info(entry: DatasetEntry, i: int, total: int, n_good: int):
    display_header(i, total, n_good)
    pr_url = f"https://github.com/{entry.metadata.repo}/pull/{entry.metadata.pr_number}"
    print(f"\nPull Request : {pr_url}\n")


def prompt_comment_suggestion(
    entry: DatasetEntry, sel: Optional[Selection], overwrite: bool
) -> bool:
    if len(entry.comments) == 0:
        return False
    # reuse existing if available and not overwriting
    if not overwrite and sel is not None and sel.comment_suggests_change is not None:
        return sel.comment_suggests_change

    for c in entry.comments:
        patch_text = entry.diffs_before[c.file]
        hunks = split_into_hunks(patch_text)
        for hunk in hunks:
            match = re.match(r"@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@", hunk)
            assert match is not None, f"{hunk} has no header apparently"
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) else 1
            old_end = old_start + old_count - 1

            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) else 1
            new_end = new_start + new_count - 1

            if (
                (
                    c.from_ is not None
                    and (old_start <= c.from_ <= old_end or new_start <= c.from_ <= new_end)
                )
                or old_start <= c.to <= old_end
                or new_start <= c.to <= new_end
            ):
                print("Hunk pointed by comment:")
                print(pretty_diff(hunk))
                break

        print(f"\nComment: {c.body}")
    return prompt_yes_no("Do the comment suggest a change?")


def show_diffs(entry):
    print("Diffs:")
    for fname, diff in entry.diffs_after.items():
        print(f"--- {fname} ---")
        print(pretty_diff(diff) if diff else "EMPTY DIFF")


def ask_diff_relevance(entry: DatasetEntry) -> bool:
    show_diffs(entry)
    print(f"Comment: {entry.comments[0].body}")
    return prompt_yes_no(f"Are {bold('any')} of these diffs related to the comment?")


def select_relevant_hunks(diff: str, comment: str) -> list[str]:
    hunks = split_into_hunks(diff)
    selected = []
    for idx, h in enumerate(hunks, 1):
        print(f"\nHunk #{idx}:")
        print(pretty_diff(h))
        print(f"Comment: {comment}")
        if prompt_yes_no(f"Is hunk #{idx} related?", default=False):
            if prompt_yes_no("Edit this hunk?", default=False):
                h = edit_hunk(h)
            selected.append(h)
    return selected


def refine_entry(
    entry: DatasetEntry, sel: Optional[Selection], overwrite: bool, check_diff: bool
) -> bool:
    if not overwrite and sel is not None and sel.diff_after_address_change is not None:
        return sel.diff_after_address_change

    diff_relevant = ask_diff_relevance(entry)
    if not diff_relevant:
        return False

    if check_diff:
        accumulated = {}
        for fname, diff in entry.diffs_after.items():
            if not diff:
                continue
            hunks = select_relevant_hunks(diff, entry.comments[0].body)
            if hunks:
                accumulated[fname] = "\n".join(hunks)
        if len(accumulated) == 0:
            return False
        entry.diffs_after = accumulated
    return True


def main(
    dataset_path: str,
    output: str,
    overwrite: bool = False,
    validation_mode: ValidationMode = ValidationMode.REFINEMENT,
    check_diff_relevance: bool = False,
):
    dataset = Dataset.from_json(dataset_path)

    if validation_mode == ValidationMode.COMMENT:
        # For comment validation, process all entries
        entries_to_process = dataset.entries
        print("Running in COMMENT VALIDATION mode - only checking if comments suggest changes")
    else:
        # For refinement validation, only process successful entries
        entries_to_process = [entry for entry in dataset.entries if entry.metadata.is_covered]
        print(
            "Running in REFINEMENT VALIDATION mode - checking both comment suggestions and implementation"
        )

    total = len(entries_to_process)
    try:
        n_good = 0
        for i, entry in enumerate(entries_to_process, 1):
            sel = entry.metadata.selection

            display_pr_info(entry, i, total, n_good)

            suggests = prompt_comment_suggestion(entry, sel, overwrite)

            if not suggests:
                entry.metadata.selection = Selection(False, None)
                continue

            if validation_mode == ValidationMode.COMMENT:
                entry.metadata.selection = Selection(
                    True,
                    sel.diff_after_address_change if sel is not None else None,
                )
                n_good += 1
            elif validation_mode == ValidationMode.REFINEMENT:
                diff_relevant = refine_entry(entry, sel, overwrite, check_diff_relevance)
                entry.metadata.selection = Selection(True, diff_relevant)
                if diff_relevant:
                    n_good += 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        print(f"Saving dataset to {output}...", end=" ", flush=True)
        dataset.to_json(output)
        print("Done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual selection of dataset")
    parser.add_argument("dataset", type=str, help="Path to the dataset file")
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=str,
        help="The path to the resulting dataset",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-evaluate existing selections",
    )
    parser.add_argument(
        "-m",
        "--mode",
        type=ValidationMode,
        default=ValidationMode.COMMENT,
        action=EnumChoicesAction,
        help=f"Validation mode: '{ValidationMode.COMMENT.value}' to only check if comments suggest changes, '{ValidationMode.REFINEMENT.value}' to check both comment suggestions and implementation. Default is '{ValidationMode.COMMENT.value}'",
    )
    parser.add_argument(
        "--check-diff-relevance",
        action="store_true",
        help="Check if each diff is related to the comment before asking if it implements the change",
    )
    args = parser.parse_args()
    main(
        args.dataset,
        args.output,
        overwrite=args.overwrite,
        validation_mode=args.mode,
        check_diff_relevance=args.check_diff_relevance,
    )
