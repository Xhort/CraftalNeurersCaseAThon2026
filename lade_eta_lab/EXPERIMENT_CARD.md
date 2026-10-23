# Experiment card

## Decision and user

A dispatcher uses the model to estimate how many minutes remain between a courier accepting a package and completing delivery. A large error can create a missed promise, delay intervention, or cause unnecessary escalation. One complete order is one evaluation case.

Better means lower mean absolute error in minutes while preserving useful output coverage. Large-error rate, run variation, deferral rate, and human review time are reported separately so the system cannot appear safer by rejecting everything.

## Core question

When does additional information from a low-reliability source make the ETA decision worse, and does a reliability-aware intervention restore useful performance on the same future deliveries?

## AI and information path

The AI is a histogram gradient-boosting regressor. Information enters through acceptance-time features: service area, area type, time of day, day of week, location-derived distance, already accepted tasks, and prior-day courier and area ETA history.

Current-order completion information creates the offline label only. It is not an input available at prediction time.

## Reference and split

The source is one LaDe city delivery CSV. Records are ordered in time and split by complete service days: 60 percent training, 20 percent validation, and 20 percent evaluation. The 3,407 evaluation orders and their outcomes remain fixed across every comparison.

Three fixed seeds—17, 29, and 43—measure variability in the injected low-quality feed and model fitting. The source is a reference, not assumed perfect ground truth.

## Degradation conditions

| Condition | What changes | Stage affected |
|---|---|---|
| Reference | No controlled defect | None |
| Stale only | Area-history values freeze at the training boundary while recorded age grows | Live input |
| Low quality only | A duplicated low-reliability feed corrupts selected duration, distance, and area-history values and reaches 55 percent of evidence | Retraining and live input |
| Combined | Stale and low-quality changes occur together | Retraining and live input |

The defects are controlled simulations of accidental failure. They are not claims that the source dataset or publisher experienced these incidents.

## Countermeasure

1. Weight training rows by recorded reliability and freshness rather than treating all records as equally trustworthy.
2. Back suspicious distance and area-history inputs toward robust training defaults.
3. Expand uncertainty with measured risk and defer high-risk outputs to a dispatcher.

## Metrics

- Mean absolute error in minutes: primary task metric; lower is better.
- Root mean squared error: makes large misses more visible.
- Share of predictions more than 15 minutes wrong: tail-usefulness check.
- Deferral rate and coverage: tracks withheld outputs.
- Standard deviation across seeds: run-to-run variability.
- Model latency and human review time: recorded separately in the application workflow.

## Measured findings

| Condition | Fix off MAE | Fix on MAE | What it means |
|---|---:|---:|---|
| Reference | 62.1 min | 62.2 min | The fix provides no ordinary-condition gain |
| Stale only | 61.5 min | 63.6 min | Stale injection does not harm this model; the fix worsens error |
| Low quality only | 64.2 min | 63.7 min | Added low-quality volume harms performance; the fix recovers about 0.5 min |
| Combined | 63.8 min | 65.0 min | The fix worsens combined-condition performance |

The review rule defers 100 percent of stale and combined evaluation orders. Therefore the current intervention does not restore useful performance and should not be deployed.

## What can and cannot be concluded

The experiment supports a narrow claim: under the documented low-quality injection, a high-volume unreliable source raises measured ETA error on this Jilin evaluation period. It does not prove that stale data always hurts, that the mitigation is generally effective, or that courier findings validate an autonomous vehicle.

## Next decision

Recalibrate the deferral rule, test region-level coverage and error, and measure reviewer effort. Only then consider a four-week one-region shadow pilot with pre-registered go/no-go thresholds.
