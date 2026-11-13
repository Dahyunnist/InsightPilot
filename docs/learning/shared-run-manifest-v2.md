# Shared run manifest v2

The existing IP offline v1 manifest and validator remain supported. `insightpilot.run_manifest` adds a public v2 normalization view for the v1 RulePlanner baseline and blocked model preflight; it does not rewrite either source record.

The public view maps actual git commit, frozen dataset and split, actual model calls, prompt/tool/scorer hashes, runtime, budget, independent gold, cost, run/trace/span IDs, status, and timestamps. A planned model remains in `planned_model_id`; a run with zero calls has `model_id=none`. Missing evidence is `null` with a `blocked`, `not_recorded`, or `not_applicable` field state. The validator rejects `unknown`, sensitive payload keys, missing state, invalid hashes, and planned-model/actual-model confusion.

The RulePlanner baseline has a completed run record, actual Python/platform/dependency and runner hashes, 0 model calls, and $0 model cost. Its frozen score remains 15/22; execution completed, evaluation outcome failed strict acceptance. Its legacy v1 manifest remains the source of truth for recorded run and trace IDs. No span was emitted.

The r3 real-model attempt stopped before a model call. Public view records actual model as `none`, planned model separately, cost $0, prior reservation as stale, and missing run/trace/span/runtime/scorer facts as blocked. It does not claim that a model evaluated any question.

`reports/ip4-v13-event-vocabulary-coverage.json` compares the shared event vocabulary (`timeout`, `retry`, `budget_exceeded`, `cancel`, `unsafe_action_blocked`, `checkpoint_resume`) with InsightPilot JSONL events (`analysis.started`, `analysis.completed`, `analysis.failed`). No trace JSONL was emitted for these two runs. Counts are zero observed; they do not prove that no such events occurred.

Rebuild public views: `.venv/Scripts/python.exe scripts/normalize_ip4_public_manifests.py`. Focused checks: `.venv/Scripts/python.exe -m unittest discover -s tests -p test_run_manifest.py`.
