# Shared run manifest v2

The existing IP offline v1 manifest and validator remain supported. `insightpilot.run_manifest` adds a public v2 normalization view for the v1 RulePlanner baseline and blocked model preflight; it does not rewrite either source record.

The public view maps actual git commit, frozen dataset and split, actual model calls, prompt/tool/scorer hashes, runtime, budget, independent gold, cost, run/trace/span IDs, status, and timestamps. A planned model remains in `planned_model_id`; a run with zero calls has `model_id=none`. Missing evidence is `null` with a `blocked`, `not_recorded`, or `not_applicable` field state. The validator rejects `unknown`, sensitive payload keys, missing state, invalid hashes, and planned-model/actual-model confusion.

## Four-repository minimum-field comparison (2026-11-14)

| Repository | Identity and versions | Data/scorer provenance | Runtime and budget | Outcome and transport evidence |
| --- | --- | --- | --- | --- |
| InsightPilot | schema/project/run/trace/span/evaluation IDs; dataset and split | dataset, gold, prompt, tool, scorer hashes | runtime digest, Python/platform/lock; cap/spent/reserved | model calls/input flag, status/outcome, timestamps, event coverage; now optional attempted/response/cost certainty |
| OpsPilot | project/run/trace/evaluation/dataset/split IDs | dataset, truth, evaluator, result, prompt/tool/scorer hashes | runtime and token/cost caps; parameters | status, cost, event and telemetry evidence |
| Papertrail | run/trace/git/dataset/split IDs | dataset, prompt, scorer hashes | runtime Python/platform/lock; budget | model ID, gold source, cost, status, start/end; no separate calls-vs-responses or field-level missingness map |
| CodePilot | pilot/date/model/execution and case IDs | agent git/tree, metadata/archive/repo/test hashes | token, time, context and cost reserve ceilings | baseline/success outcome; no shared run/trace IDs or generic response-count/cost-certainty field |

Minimum portable fields are identity and schema version, dataset/split hashes, scorer/runtime, budget and cost certainty, model request/response counts, outcome/status, timestamps, and explicit missing-field state. InsightPilot lacked a distinct request-attempt count, provider-response count, and cost-certainty vocabulary. Public v2 now accepts optional `request_outcome` with those counts and `cost_status`; old v1/v2 manifests remain valid. Unknown actual cost must serialize as null. This is an additive compatibility check; no other repository changed.

The RulePlanner baseline has a completed run record, actual Python/platform/dependency and runner hashes, 0 model calls, and $0 model cost. Its frozen score remains 15/22; execution completed, evaluation outcome failed strict acceptance. Its legacy v1 manifest remains the source of truth for recorded run and trace IDs. No span was emitted.

The r3 real-model attempt stopped before a model call. Public view records actual model as `none`, planned model separately, cost $0, prior reservation as stale, and missing run/trace/span/runtime/scorer facts as blocked. It does not claim that a model evaluated any question.

`reports/ip4-v13-event-vocabulary-coverage.json` compares the shared event vocabulary (`timeout`, `retry`, `budget_exceeded`, `cancel`, `unsafe_action_blocked`, `checkpoint_resume`) with InsightPilot JSONL events (`analysis.started`, `analysis.completed`, `analysis.failed`). No trace JSONL was emitted for these two runs. Counts are zero observed; they do not prove that no such events occurred.

Rebuild public views: `.venv/Scripts/python.exe scripts/normalize_ip4_public_manifests.py`. Focused checks: `.venv/Scripts/python.exe -m unittest discover -s tests -p test_run_manifest.py`.
