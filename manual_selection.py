from dataset import Dataset, Selection
import argparse, os, re, click
from enum import Enum
from utils import prompt_yes_no

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
    if after is None:
        return ""
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
        entries_to_process = [entry for entry in dataset.entries if entry.metadata.successful]
        print(
            "Running in REFINEMENT VALIDATION mode - checking both comment suggestions and implementation"
        )

    try:
        n_good = 0
        for i, entry in enumerate(entries_to_process, 1):
            sel = entry.metadata.selection
            # Skip or count already processed entries if not overwriting
            if not overwrite and sel is not None:
                if (
                    validation_mode == ValidationMode.COMMENT
                    and sel.comment_suggests_change is not None
                ):
                    n_good += int(sel.comment_suggests_change)
                    continue
                if (
                    validation_mode == ValidationMode.REFINEMENT
                    and sel.diff_after_address_change is not None
                ):
                    n_good += int(sel.diff_after_address_change)
                    # We'll re-ask diffs if needed below
                # If selection exists but incomplete for this mode, proceed

            # Header info
            print("#" * os.get_terminal_size().columns)
            print(f"# good PRs: {n_good}/{i} ({n_good/i:.2%})")
            print(f"Current PR: {i}/{len(entries_to_process)} ({i/len(entries_to_process):.2%})")
            pr_url = f"https://github.com/{entry.metadata.repo}/pull/{entry.metadata.pr_number}"
            print(f"\nPull Request : {pr_url}")

            is_code_related = any(file.file.endswith('.java') for file in entry.comments)
            for comment in entry.comments:
                print("\nComment:", comment.body)

                # Comment suggestion check
                if not overwrite and sel is not None and sel.comment_suggests_change is not None:
                    suggests = sel.comment_suggests_change
                else:
                    suggests = prompt_yes_no("Does this comment suggest a change?")

                if not suggests:
                    print("Doesn't suggest any change, skipping...")
                    entry.metadata.selection = Selection(
                        comment_suggests_change=False,
                        diff_after_address_change=None,
                        is_code_related=any(file.file.endswith('.java') for file in entry.comments),
                    )
                    break

                if validation_mode == ValidationMode.COMMENT:
                    entry.metadata.selection = Selection(
                        comment_suggests_change=True,
                        diff_after_address_change=sel.diff_after_address_change
                        if sel is not None
                        else None,
                        is_code_related=is_code_related,
                    )
                    n_good += 1
                    break

                # REFINEMENT mode: show all diffs first

                # Initial relevance query
                if not overwrite and sel is not None and sel.diff_after_address_change is not None:
                    any_relevant = sel.diff_after_address_change
                else:
                    print("Diffs:")
                    for f, diff in entry.diffs_after.items():
                        if diff is None:
                            print(f, "EMPTY DIFF")
                            continue
                        print(f"--- {f} ---")
                        print(pretty_diff(diff))
                    any_relevant = prompt_yes_no("Are any of these diffs related to the comment?")

                if not any_relevant:
                    print("No diffs relevant, skipping...")
                    entry.metadata.selection = Selection(
                        comment_suggests_change=True,
                        diff_after_address_change=False,
                        is_code_related=is_code_related,
                    )
                    break

                # Ask which diffs if detailed relevance requested
                relevant_diffs = {}
                if check_diff_relevance:
                    for f, diff in entry.diffs_after.items():
                        if diff is None:
                            continue
                        hunks = split_into_hunks(diff)
                        if not hunks:
                            continue

                        print(f"\n--- {f} has {len(hunks)} hunks ---")

                        selected_hunks: list[str] = []
                        for idx, hunk in enumerate(hunks, 1):
                            print(f"\nHunk #{idx}:")
                            print(pretty_diff(hunk))
                            print(f"Comment: {comment.body}")
                            if prompt_yes_no(f"  → Is hunk #{idx} related to the comment?"):
                                if prompt_yes_no(
                                    f"  → Do you want to edit this hunk?", default=False
                                ):
                                    new_hunk = edit_hunk(hunk)
                                    selected_hunks.append(new_hunk)
                                else:
                                    selected_hunks.append(hunk)

                        if len(selected_hunks) > 0:
                            # join back into one diff string for storage
                            relevant_diffs[f] = "\n".join(selected_hunks)

                    if len(relevant_diffs) == 0:
                        print("No relevant diffs found, skipping...")
                        entry.metadata.selection = Selection(
                            comment_suggests_change=True,
                            diff_after_address_change=False,
                            is_code_related=is_code_related,
                        )
                        break

                    print("\nRelevant diffs:")
                    for f, d in relevant_diffs.items():
                        print(f"--- {f} ---")
                        print(pretty_diff(d))
                else:
                    relevant_diffs = entry.diffs_after

                entry.diffs_after = relevant_diffs
                entry.metadata.selection = Selection(
                    comment_suggests_change=True,
                    diff_after_address_change=True,
                    is_code_related=is_code_related,
                )
                if len(relevant_diffs) > 0:
                    n_good += 1
                break
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
        choices=[mode.value for mode in ValidationMode],
        default='comment',
        help="Validation mode: 'comment' to only check if comments suggest changes, 'refinement' to check both comment suggestions and implementation. Default is 'comment'",
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
        validation_mode=ValidationMode(args.mode),
        check_diff_relevance=args.check_diff_relevance,
    )
