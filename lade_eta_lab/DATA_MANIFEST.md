# Data manifest

## Source and selected scope

| Field | Recorded value |
|---|---|
| Dataset | LaDe last-mile delivery dataset |
| Publisher | Cainiao-AI |
| Source URL | https://huggingface.co/datasets/Cainiao-AI/LaDe |
| Local retrieval date | 17 October 2026 |
| Selected file | `delivery/delivery_jl.csv` |
| Local SHA-256 | `12e2cf4664dd5b4475d39dddee8872f5a03b3082f08f0eece7f103baee6c6e73` |
| City | Jilin |
| Source rows | 31,415 |
| Unique order identifiers | 31,415 |
| Rows used after outcome checks | 27,431 |
| Excluded rows | 3,984 with an unparseable time or a duration outside 1–360 minutes |
| Evaluation date range | 12 May–26 October 2020 |
| Raw data redistributed | No |

## Fields and units used

| Field | Meaning in this experiment | Unit or type |
|---|---|---|
| `order_id` | Independent delivery case; renamed `package_id` internally | Identifier |
| `accept_time` | Decision time | Local month-day time, interpreted in 2020 |
| `delivery_time` | Offline outcome used to calculate duration | Local month-day time, interpreted in 2020 |
| `courier_id` | Courier grouping for prior-day history and workload | Identifier |
| `region_id` | Service-area grouping | Identifier |
| `lng`, `lat` | Delivery location | Coordinates as provided by source |
| `accept_gps_lng`, `accept_gps_lat` | Acceptance location when present | Coordinates as provided by source |
| `aoi_type` | Encoded area type | Category |

Completion-time fields are used only to create the offline outcome and prior-day aggregates. The current order’s delivery completion is never used as an acceptance-time model input.

## Missing values and filters

- Acceptance GPS coordinates are missing for about 3.04 percent of source rows.
- When acceptance GPS is missing, the distance calculation falls back to the delivery coordinates, producing a zero-distance proxy. The model pipeline then median-imputes any remaining numeric missing values.
- Delivery durations must fall between 1 and 360 minutes. This rule is fixed before comparing conditions.
- No raw row is labeled “bad” because it belongs to a minority region or unusual operating case.

## Chronological split

Whole service days stay together so outcomes from the future cannot leak backward.

| Split | Dates | Rows | Distinct days | Use |
|---|---|---:|---:|---|
| Training | 12 May–17 August 2020 | 16,863 | 96 | Fit model and reference history |
| Validation | 18 August–18 September 2020 | 7,161 | 32 | Residual interval and model checks |
| Evaluation | 19 September–26 October 2020 | 3,407 | 33 | Fixed unseen cases for all eight comparisons |

## Introduced experimental changes

These changes are simulations of plausible accidental failures. They are not claims about defects in the original LaDe operation.

| Condition | Stage affected | Changed fields | Fixed parameters | Plausible failure represented |
|---|---|---|---|---|
| Stale | Live inputs | `area_historical_eta`, `data_age_days` | Freeze at training boundary; age grows daily | An area-statistics job stops refreshing |
| Low quality | Retraining and live inputs | Training duration labels, `distance_km`, `area_historical_eta`, `source_reliability`, `low_quality_share` | Maximum feed share 55%; seeds 17, 29, 43 | A fast, low-reliability feed contributes more records than the trusted source |
| Combined | Retraining and live inputs | All stale and low-quality fields above | Same parameters and seeds | Refresh failure and high-volume noisy feed occur together |

Each condition retains the same evaluation order identifiers and true delivery outcomes. The modification log is also available as `results/modification_log.csv`.

## Rights statement

The repository metadata reports Apache-2.0, while the author paper reports CC BY-NC 4.0. The raw city CSV remains local and excluded from Git. Before any public redistribution, the team must resolve which terms control the data file and retain both source references in the submission.

## Untouched reference

The local source CSV is never edited. Feature preparation and introduced changes operate on in-memory copies. Its SHA-256 is recorded above so a future run can verify that the reference file has not changed.
