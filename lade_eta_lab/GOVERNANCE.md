# Governance and pilot decision

## Current decision

Do not deploy the prototype. The low-quality-feed experiment shows measurable harm, but the current mitigation does not consistently recover ETA accuracy and defers all orders in stale and combined conditions. That combination would create unacceptable dispatcher workload and uncertain operational value.

## Accountability

| Responsibility | Accountable role | Evidence and decision checkpoint |
|---|---|---|
| Source rights, provenance, and retention | Data steward | Signed data manifest and confirmed controlling license before any redistribution |
| Data age, source identity, and quality monitoring | Data-quality owner | Daily age, reliability, missingness, source-volume, and schema report |
| Model features, validation, and retraining | Model owner | Reproducible run package, subgroup review, and approved retraining trigger |
| Dispatcher review and corrections | Operations lead | Timestamped action log, weekly override sample, and workload report |
| Security and system access | Technical lead | Local-only prototype, no credentials in source, controlled write access to logs |
| Shadow-pilot go/no-go decision | Pilot sponsor | Joint accuracy, coverage, subgroup, workload, and incident review |

Named people should be assigned to these roles before a pilot begins. A role is used here because the competition artifact should not invent personal authority.

## Required deferral

The system must withhold an operational recommendation and request qualified review when any approved rule is met:

- Source identity or provenance is missing.
- A required feature is older than the approved maximum age.
- Reliability or schema checks fail.
- The prediction range is too wide for the decision being made.
- The order is outside the evaluated city, time window, or operating context.
- Monitoring detects an unevaluated failure mode or material subgroup harm.

Deferral is not recovery. Coverage and review time must be reported alongside accuracy, and a configuration that defers nearly every case fails the usefulness requirement.

## Human correction path

1. The dispatcher sees the model ETA, prediction range, data age, feed share, reliability, and plain-language review reason.
2. The dispatcher accepts, corrects, or escalates the ETA.
3. The backend logs the original prediction, action, corrected value, note, reviewer label, and review time.
4. The operations lead reviews override patterns weekly. Corrections do not automatically retrain the model.
5. The model owner decides whether verified corrections qualify for a controlled retraining set.

## Stop conditions

Stop the experiment or shadow pilot if:

- Mean absolute error or large-error rate exceeds the approved ceiling for two consecutive review windows.
- Coverage falls below the approved floor, including excessive deferral.
- Review workload exceeds available qualified staffing.
- A subgroup shows material harm that the team cannot explain and bound.
- Source terms, lineage, or required fields cannot be verified.
- An incident shows the interface can cause an operator to mistake a deferred result for an approved decision.

## Bounded next step

First, recalibrate the review rule so it targets high-risk cases rather than every stale case. Add a region-level subgroup table and measure dispatcher review time in a small peer walkthrough. If accuracy, coverage, subgroup behavior, and workload meet pre-registered thresholds, run a four-week shadow pilot in one region. The model owner and operations lead present the post-pilot evidence to the pilot sponsor for a written go/no-go decision.

The system remains advisory during shadow mode. It must not change customer promises, routes, staffing, or live operations.
