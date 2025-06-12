# CRAB: Code Review Automated Benchmark

CRAB (Code Review Automated Benchmark) is a high-quality dataset and extraction pipeline designed to evaluate automated code-review tools on two complementary tasks:

1. **Review Comment Generation**
   Given a code snapshot before review, generate natural-language comments emulating human reviewers.
1. **Code Refinement (Revised Code Generation)**
   Given the same snapshot plus a reviewer’s comment, generate the revised code implementing that feedback.&#32;

CRAB focuses on **Java** projects, rigorously curating pull-request “triplets” of

- **submitted_code** (pre-review code)
- **reviewer_comment** (validated natural-language feedback, with paraphrases)
- **revised_code** (post-review implementation, validated via tests)&#32;

## Features

- **Automated Extraction Pipeline** (`pull_requests.py`)

  - Clones GitHub repositories, locates PRs with a single review comment, and extracts diffs before/after the comment
  - Builds and tests each snapshot in Docker (Maven & Gradle support)
  - Generates JaCoCo coverage reports to ensure revised code covers the commented lines

- **Manual Validation Tools** (`manual_selection.py`)

  - Interactive review to mark whether comments suggest changes and whether post-comment diffs address them

- **Serialization & Task Extraction** (`dataset.py`, `extract_correct_predictions.py`)

  - Produce JSON datasets for:

    - **Full** (all validated triplets)
    - **Comment Generation**
    - **Code Refinement**
    - **Web App** export format

- **Utility Modules**

  - **`handlers.py`**: abstract and concrete build/test handlers (Maven, Gradle)
  - **`utils.py`**: Git/GitHub helpers, BLEU-based paraphrase filtering, logging

## Installation

1. **Clone this repository**

   ```bash
   git clone https://github.com/karma-riuk/crab
   cd crab
   ```

1. *(Optional)* **Create Python Environement**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

1. **Install Python dependencies**

   ```bash
   pip install -r requirements.txt
   ```

1. **Docker images**

   The repository includes two Dockerfiles (`maven.Dockerfile` and `gradle.Dockerfile`) at its root. Build the images locally from this directory:

   ```bash
   # Build the Maven handler image
   docker build -f maven.Dockerfile -t crab-maven .

   # Build the Gradle handler image
   docker build -f gradle.Dockerfile -t crab-gradle .
   ```

## Usage

Run the script to generate the CRAB dataset triplets:

```sh
python pull_requests.py [CSV_FILE] [options]
```

- **CSV_FILE**: Path to the input CSV listing repositories (output of `clone_repos.py`).

### Options

| Parameter | Default | Required | Description |
| - | - | - | - |
| `CSV_FILE` | — | Yes | The CSV file containing the list of GitHub repos to process. |
| `-o`, <br>`--output` | `./dataset.json` | No | Path where the resulting JSON dataset will be saved. |
| `-r`, <br>`--repos` | `./results/` | No | Directory under which repos will be (or already are) cloned. |
| `-c`, <br>`--cache` | *None* | No | Path to a previous run’s JSON output to resume from (caches processed PRs). |
| `-a`, <br>`--archive-destination` | `./dataset/archives` | No | Directory where per-PR archives (tar.gz) will be stored. |
| `-s`, <br>`--sort-by` | *None* | No | Column name in the CSV by which to sort repos before processing. |
| `--only-repo` | *None* | No | Process only the specified repo (format: `owner/name`), ignoring all others in the CSV. |
| `--cache-requests` | `false` | No | If set, caches GitHub API requests (using `requests_cache`) to speed up reruns at the risk of stale data. |
| `--max-workers` | *None* (monothreaded) | No | Number of parallel workers for processing repos. If omitted, the script runs in a single thread. |

**Example**

```sh
python pull_requests.py my_repos.csv \
  --output=data/triplets.json \
  --repos=./cloned_repos/ \
  --archive-destination=./archives/ \
  --cache-requests \
  --max-workers=4
```

This will:

1. Read `my_repos.csv` for the list of GitHub repositories.
1. Clone any missing repos under `./cloned_repos/`.
1. Process each pull request, archiving the base and merged states under `./archives/`.
1. Save the combined dataset to `data/triplets.json`.
1. Cache GitHub API calls for faster subsequent runs.
1. Use 4 parallel workers to speed up processing.

### 2. Run manual validation

Run the manual selection script to validate or refine your dataset entries:

```sh
python manual_selection.py [DATASET_FILE] -o OUTPUT [options]
```

- **DATASET_FILE**: Path to the input JSON dataset (e.g. output of your preprocessing step).
- **-o, --output**: Path where the updated dataset JSON will be saved.

### Options

| Parameter | Default | Required | Description |
| - | - | - | - |
| `DATASET_FILE` | — | Yes | Path to the dataset JSON file to process. |
| `-o`, <br>`--output` | — | Yes | Path where the resulting dataset (after manual selection/refinement) will be written. |
| `--overwrite` | *false* | No | If set, re-evaluates and overwrites any existing `Selection` entries in the dataset. |
| `-m`, <br>`--mode` | `comment` | No | Validation mode to run in:<br> • `comment` – only check if comments suggest a change.<br> • `refinement` – check comment suggestions and whether diffs implement them. |
| `--check-diff-relevance` | *false* | No | If set (only in `refinement` mode), first ask whether each diff is related to the comment before prompting for refinement. |

### 3. Serialize to JSON for modeling

Load and process a dataset JSON, optionally add paraphrases, and serialize it in various formats:

```sh
python dataset.py [FILENAME] [options]
```

- **FILENAME**: Path to the input JSON file to load (e.g., output of a previous run).

### Options

| Parameter | Default | Required | Description |
| - | - | - | - |
| `FILENAME` | — | Yes | Path to the dataset JSON file to load. |
| `-o`, <br>`--output` | `output.json` | No | Path where the processed dataset (or archive) will be saved. |
| `-p`, <br>`--paraphrases` | *None* | No | CSV file containing generated paraphrases. Must include a `paraphrases` column with lines of the form `Paraphrase#N: <text>`. When provided, each paraphrase will be scored and (optionally) appended to its comment. |
| `-t`, <br>`--output_type` | `full` | No | Type of output to generate: <br> • `full` – dump the entire dataset as JSON.<br> • `comment_gen` – dump only entries whose comments suggest changes, as a ZIP of JSON (with `_with_context` or `_no_context`).<br> • `code_refinement` – dump entries both covered and addressed, as a ZIP.<br> • `webapp` – dump minimal fields for webapp. |
| `-a`, <br>`--archives` | *None* | No | Root directory where per-PR archives (tar.gz) live. Relevant only for `comment_gen` or `code_refinement` outputs; will be bundled into the ZIP under `context/`. |
| `--remove-non-suggesting` | *false* | No | When output type is `full`, drop entries whose comments do *not* suggest a change. |

### Examples

**Basic full dump:**

```sh
python dataset.py data/raw_dataset.json
```

**Add paraphrases and overwrite default output path:**

```sh
python dataset.py data/raw_dataset.json \
  -o data/with_paraphrases.json \
  -p paraphrases.csv
```

**Generate a ZIP for code-refinement with context archives:**

```sh
python dataset.py data/raw_dataset.json \
  -o outputs/code_refinement.zip \
  -t code_refinement \
  -a ./archives/
```

This will:

1. Load `data/raw_dataset.json` into memory.
1. If `-p paraphrases.csv` is given, read paraphrases, score them, and append non-redundant ones to each comment.
1. Serialize entries according to `--output_type`.
1. Bundle required archives (if any) into the resulting ZIP or write JSON to the specified `--output`.

### 4. Extract “ground truth” references

Run the script to extract “exact prediction” JSONs for comment‐generation, code‐refinement, or paraphrase tasks:

```sh
python extract_correct_predictions.py DATASET_JSON [options]
```

- **DATASET_JSON**: Path to the input dataset JSON file.

### Options

| Parameter | Default | Required | Description |
| - | - | - | - |
| `DATASET_JSON` | — | Yes | Path to the dataset JSON to process. |
| `-o`, <br>`--output` | `exact_predictions_<type>.json` | No | Path for the output JSON file. If omitted, defaults to `exact_predictions_<output-type>.json`. |
| `-a`, <br>`--archives` | — | Only for `code_refinement` | Directory where per-PR tar.gz archives live. Required when `--output-type=code_refinement` so merged file contents can be extracted. |
| `-t`, <br>`--output-type` | `comment_gen` | No | Which extraction to perform:<br> • `comment_gen` – pull file+location+body for commenting tasks.<br> • `code_refinement` – extract post-merge file contents for code tasks.<br> • `paraphrases` – dump comments+before-PR files for paraphrase creation. |

### OutputType Values

| Name | Value | Meaning |
| - | - | - |
| `COMMENT_GEN` | `comment_gen` | Extracts predicted comment locations & bodies to feed a comment‐generation model. |
| `CODE_REFINEMENT` | `code_refinement` | Extracts merged file snapshots for entries that both cover and address changes, to feed a refinement model. |
| `FOR_PARAPHRASES` | `paraphrases` | Extracts original comments plus “before-PR” file contents for paraphrase generation. |

### Examples

**1. Default comment-generation extraction**

```sh
python extract_correct_predictions.py data/dataset.json \
  -o predictions_comment.json
```

This reads `data/dataset.json` and writes all entries whose comments suggest changes to `predictions_comment.json`.

______________________________________________________________________

**2. Code-refinement extraction**

```sh
python extract_correct_predictions.py data/dataset.json \
  --output refined_files.json \
  --output-type code_refinement \
  --archives ./archives/
```

This will locate each merged PR archive under `./archives/`, extract the post-merge file contents for entries that both cover and address changes, and save them to `refined_files.json`.

______________________________________________________________________

**3. Paraphrase data extraction**

```sh
python extract_correct_predictions.py data/dataset.json \
  -t paraphrases \
  -o comments_for_para.json
```

This dumps comment bodies plus “before-PR” file snapshots for all entries suggesting changes, suitable for paraphrase modeling.

## Contributing

1. **Issue Tracker**: Please file issues for bugs or feature requests.
1. **Pull Requests**: Fork, create a topic branch, and submit a PR. Please include tests or validations where applicable.
1. **Extending Build Support**: To add a new build system (e.g., Ant, Bazel), subclass `BuildHandler` in `handlers.py` and provide the commands and container image.
