# Offline baseline run manifest

New IP4 v13 RulePlanner baselines use `insightpilot.run_manifest.validate_run_manifest` before either report is written. The companion manifest follows OP common fields plus evaluation/domain/truth/result fingerprints. It declares `model_id=none`, independent gold, null prompt/tool/model-token fields, a SHA-256 runtime digest, and zero estimated model cost. Validation failure raises before report writes. Existing historical reports remain readable without migration.

Synthetic positive and negative schema tests cover required fields, SHA formats, status, cost, and no-model constraints. Writer validates before creating either manifest or result report; a negative test proves invalid cost leaves no file. This v1 contract remains supported. The additive public v2 view is documented in `shared-run-manifest-v2.md`; it keeps blocked-attempt receipt facts separate and leaves missing run/trace/span fields null with explicit blocked status.
