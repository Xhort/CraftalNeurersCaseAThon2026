# AI use disclosure

## Evaluated AI capability

The demonstrated AI is a supervised histogram gradient-boosting regression model. It predicts minutes from package acceptance to delivery using acceptance-time location, time, area, courier-history, and workload features. The experiment evaluates how stale inputs and a high-volume low-reliability feed change that model’s predictions, error, intervals, and review decisions over an unseen chronological period.

The tested countermeasure down-weights unreliable training rows, moves suspicious inference values toward robust defaults, and flags high-risk outputs for human review. The results do not support a deployment claim: improvement is modest under low-quality data, performance worsens in other conditions, and the current review policy over-defers stale cases.

## Generative AI assistance

OpenAI Codex assisted with code structure, interface implementation, documentation, and test development. Team members remain responsible for reviewing the code, rerunning the experiment, verifying every reported value, checking source terms, naming accountable owners, and making final competition and governance decisions.

No synthetic preview result is presented as measured LaDe evidence. Introduced data defects are labeled controlled simulations and are not described as observed failures in the source organization.
