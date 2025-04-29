from dataset import Dataset, Selection
import argparse, os
from enum import Enum
from utils import prompt_yes_no


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


def main(
    dataset_path: str,
    overwrite: bool = False,
    validation_mode: ValidationMode = ValidationMode.REFINEMENT,
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
            if entry.metadata.selection and not overwrite:
                if entry.metadata.selection.good:
                    n_good += 1
                continue  # Skip already processed
            print("#" * os.get_terminal_size().columns)

            pr_url = f"https://github.com/{entry.metadata.repo}/pull/{entry.metadata.pr_number}"
            print(f"# good PRs: {n_good}/{i} ({n_good/i:.2%})")
            print(f"Current PR: {i}/{len(entries_to_process)} ({i/len(entries_to_process):.2%})")
            print(f"\nPull Request : {pr_url}")

            for comment in entry.comments:
                print("\nComment:", comment.body)
                change = prompt_yes_no("Does this comment suggest a change?")

                if not change:
                    entry.metadata.selection = Selection(
                        comment_suggests_change=False,
                        diff_after_address_change=None,
                        good=False,
                    )
                    break

                if validation_mode == ValidationMode.COMMENT:
                    # In comment validation mode, we only check if the comment suggests a change
                    entry.metadata.selection = Selection(
                        comment_suggests_change=True,
                        diff_after_address_change=None,
                        good=True,
                    )
                    n_good += 1
                    break
                else:
                    # In refinement validation mode, we also check if the diff implements the change
                    for file, diff in entry.diffs_after.items():
                        if diff is None:
                            print(file, "EMPTY DIFF")
                            continue
                        print(file, pretty_diff(diff))

                    applied = prompt_yes_no("Does this diff implement the change suggested?")

                    entry.metadata.selection = Selection(
                        comment_suggests_change=True,
                        diff_after_address_change=applied,
                        good=applied,
                    )
                    if applied:
                        n_good += 1

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        print(f"Saving dataset to {dataset_path}...", end=" ", flush=True)
        dataset.to_json(dataset_path)
        print("Done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual selection of dataset")
    parser.add_argument("dataset", type=str, help="Path to the dataset file")
    parser.add_argument(
        "-o", "--overwrite", action="store_true", help="Re-evaluate existing selections"
    )
    parser.add_argument(
        "-m",
        "--mode",
        # type=lambda x: ValidationMode(x),
        # choices=[mode.value for mode in ValidationMode],
        default='comment',
        help="Validation mode: 'comment' to only check if comments suggest changes, 'refinement' to check both comment suggestions and implementation. Default is 'comment'",
    )
    args = parser.parse_args()
    main(args.dataset, overwrite=args.overwrite, validation_mode=args.mode)
