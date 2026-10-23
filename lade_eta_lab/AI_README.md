# AI Project File Guide

Use this page when you need to quickly understand the project. It uses simple descriptions and tells you which files matter first.

## Start here

You normally use only these three files:

1. `run_experiments.py` — runs the AI experiment and creates the results.
2. `app.py` — opens the visual dashboard and dispatcher screen.
3. `config.py` — contains the main experiment settings.

Typical commands:

```bash
source .venv/bin/activate
python run_experiments.py --city jl --max-rows 60000 --seeds 17 29 43
streamlit run app.py
```

## Python files

### `app.py`

**Purpose:** Shows the results in a website-like dashboard.

It displays charts, model error, warnings, prediction ranges, and the dispatcher review buttons.

**Use it when:** You want to demonstrate the project.

**Change it when:** You want to change the dashboard's appearance or wording.

### `run_experiments.py`

**Purpose:** Starts the complete AI experiment.

It loads the selected data and tells the other files to run all four data conditions with the fix off and on.

**Use it when:** You need new results.

**Change it when:** You want new command-line options. Most experiment settings belong in `config.py`, not here.

### `config.py`

**Purpose:** Stores the main settings and folder locations.

Important settings include the random seeds, train/test percentages, unreliable-feed share, stale-data limit, and maximum row count.

**Use it when:** You want to inspect the experiment setup.

**Change it carefully:** Changing these values changes the experiment and must be documented.

### `data.py`

**Purpose:** Downloads and loads the LaDe delivery data.

It also makes fake demonstration data when you want to test the code without using LaDe.

**Use it when:** You need to download a city file.

Example:

```bash
python data.py --download --city jl
```

**Important:** Fake data is labeled `SYNTHETIC_DEMO` and must not be presented as real evidence.

### `features.py`

**Purpose:** Turns delivery rows into information the AI can use.

Examples include time of day, distance, workload, region history, and courier history. It also calculates the answer the AI is learning to predict: delivery time in minutes.

It splits data in time order and keeps entire days together.

**Change it carefully:** A bad feature can accidentally reveal the future to the AI and make results look falsely good.

### `scenarios.py`

**Purpose:** Creates the four test conditions.

- `reference` — normal data.
- `stale` — old information stops updating.
- `low_quality` — an unreliable feed adds incorrect information.
- `combined` — stale and low-quality data happen together.

**Use it when:** You want to understand exactly how the project damages the data.

**Change it carefully:** The amount of damage must be justified in the presentation.

### `model.py`

**Purpose:** Builds and uses the actual AI model.

It trains the ETA model, makes predictions, creates prediction ranges, calculates errors, and compares the AI with a simple historical-average baseline.

**Use it when:** You want to change the type or settings of the AI model.

### `mitigation.py`

**Purpose:** Contains the attempted fix.

It gives less influence to old or unreliable information, makes suspicious inputs less extreme, and decides when a prediction should be reviewed by a person.

**Use it when:** You want to understand why the fix helped or hurt.

**Change it carefully:** Do not tune it only until it wins. A failure or tradeoff should be reported honestly.

### `evaluate.py`

**Purpose:** Measures the complete experiment fairly.

It runs all eight comparisons:

```text
4 data conditions × fix off/on
```

It calculates errors, creates daily timeline results, saves predictions, and saves the trained models.

**Use it when:** You want to add or change measurements.

### `tests/test_pipeline.py`

**Purpose:** Checks that important parts of the code still work.

It checks the features, time split, matching test orders, and model output.

Run it with:

```bash
python -m pytest
```

**Use it after:** Any change to the Python code.

## Instructions and record-keeping files

### `README.md`

**Purpose:** Full setup and operating instructions.

Read this when you need detailed commands or an explanation of the experiment.

### `AI_README.md`

**Purpose:** This quick, simple file guide.

Update this guide whenever an important file is added, removed, or given a new purpose.

### `DATA_MANIFEST.md`

**Purpose:** Records exactly where the data came from and what was changed.

Your team must fill in every `TODO` before presenting final results.

### `GOVERNANCE.md`

**Purpose:** Records who is responsible for the data, model, human review, and pilot decision.

Your team must replace the placeholder owners and limits.

### `AI_USE_DISCLOSURE.md`

**Purpose:** Explains which AI is being tested and how generative AI helped build the project.

Your team must make sure it accurately describes the final work.

### `requirements.txt`

**Purpose:** Lists the Python packages needed to run the project.

Install them with:

```bash
python -m pip install -r requirements.txt
```

### `pyproject.toml`

**Purpose:** Tells the test system where the tests are and how to run them.

You normally do not need to change this file.

### `.gitignore`

**Purpose:** Prevents large data, generated results, trained models, and the private Python environment from being committed to Git.

You normally should not remove these protections.

## Folders and generated files

### `data/raw/`

**Purpose:** Holds downloaded LaDe CSV files.

These files are large and should not be uploaded to the repository.

### `results/summary.csv`

**Purpose:** The main results table. It shows average performance and variation across seeds.

**Look here first** when deciding what numbers belong in the presentation.

### `results/runs.csv`

**Purpose:** Shows every individual seed run instead of only the averages.

Use it to check whether a result is stable or happened only once.

### `results/timeline.csv`

**Purpose:** Shows model error, data age, unreliable-feed share, and review rate over time.

The dashboard uses this file for its timeline chart.

### `results/predictions.csv`

**Purpose:** Stores individual test predictions for the dispatcher demonstration.

It includes the ETA, range, warning reason, data age, and whether human review is required.

### `results/experiment_config.json`

**Purpose:** Records the settings used for the latest experiment.

Keep it with the results so the experiment can be repeated.

### `results/overrides.csv`

**Purpose:** Records dispatcher actions made in the dashboard.

This file appears after someone accepts, overrides, or escalates a prediction.

### `artifacts/`

**Purpose:** Holds the trained AI model files.

These are generated automatically. Do not edit them by hand.

### `.venv/`

**Purpose:** Holds the project's private Python installation and packages.

Activate it before running the project. Do not edit its contents.

### `.pytest_cache/` and `__pycache__/`

**Purpose:** Temporary files created automatically by Python and the tests.

You can ignore them.

## Quick decision guide

| If you want to… | Use this file |
|---|---|
| Run the complete experiment | `run_experiments.py` |
| Open the presentation dashboard | `app.py` |
| Change experiment settings | `config.py` |
| Download or inspect data loading | `data.py` |
| Change what the model learns from | `features.py` |
| Change how data is damaged | `scenarios.py` |
| Change the AI model | `model.py` |
| Change the fix or review rules | `mitigation.py` |
| Change the measurements | `evaluate.py` |
| Check that everything works | `tests/test_pipeline.py` |
| Find the main results | `results/summary.csv` |
| Prepare data documentation | `DATA_MANIFEST.md` |
| Prepare ownership and safety rules | `GOVERNANCE.md` |

