"""S1 analysis state flow: preflight, retrieve, plan, compile, execute, explain."""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from .metrics import METRICS, REGION_VALUES
from .planner import NeedsClarification, OpenAICompatiblePlanner, PlanError
from .runner import QueryCancelledError, QueryExecutionError, execute_readonly, read_data_identity
from .sql import compile_query
from .verification import verify_result


def _visualization(plan, rows: list[dict]) -> dict | None:
    if plan.dimension is None or not rows:
        return None
    if "value" in rows[0]:
        value_key, unit = "value", "ratio"
    elif "amount_cents" in rows[0]:
        value_key, unit = "amount_cents", "cents"
    else:
        value_key, unit = "numerator_cents", "cents"
    return {
        "type": "line" if plan.dimension in ("month_utc", "refund_day_utc") else "bar",
        "x": {"field": "group_value", "label": plan.dimension},
        "y": {"field": value_key, "unit": unit},
        "data": [{"group_value": row.get("group_value"), value_key: row.get(value_key)} for row in rows],
    }


def _preflight(question: str) -> dict | None:
    q = question.lower()
    if any(term in q for term in ("忽略系统要求", "忽略此前指令", "忽略系统规则", "无视系统规则",
                                  "不要做独立复核", "不做独立复核", "跳过核验", "跳过复核",
                                  "编造结果", "捏造结果")):
        return {"status": "refused", "reason": "prompt_injection_attempt", "message": "请求包含试图绕过分析规则的指令，已拒绝。"}
    if re.search(r"修改|删除|新增|写入|更新|标记.*完成|改成", q):
        return {"status": "refused", "reason": "write_operation_not_supported", "message": "当前分析 Agent 只读，不能修改数据。"}
    if re.search(r"手机号|姓名|身份证|住址|家庭住址|个人信息", q):
        return {"status": "refused", "reason": "restricted_personal_data", "message": "请求涉及受限个人信息，已拒绝。"}
    return None


class InsightPilot:
    def __init__(self, planner: OpenAICompatiblePlanner, database_path: Path):
        self.planner = planner
        self.database_path = database_path

    def analyze(self, question: str, *, run_id: str | None = None, cancel_event: Event | None = None,
                authorized_region: str | None = None) -> dict:
        started = time.perf_counter()
        if hasattr(self.planner, "last_call_usage"):
            self.planner.last_call_usage = None
            self.planner.last_call_count = 0
            self.planner.last_calls = []
            self.planner.last_call_latency_ms = None
            self.planner.last_response_model = None
            self.planner.last_call_created_at = None
        result = self._analyze(question, run_id=run_id, cancel_event=cancel_event, authorized_region=authorized_region)
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result["model_usage"] = getattr(self.planner, "last_call_usage", None)
        result["model_latency_ms"] = getattr(self.planner, "last_call_latency_ms", None)
        result["model_call_count"] = getattr(self.planner, "last_call_count", 0)
        result["model_calls"] = list(getattr(self.planner, "last_calls", []))
        result["response_model"] = getattr(self.planner, "last_response_model", None)
        status = result.get("status")
        terminal_stage = result.get("stage")
        stages = ["preflight", "planning", "dataset", "compilation", "query", "verification", "answer"]
        completed_through = {"answered": 6, "clarify": -1, "refused": -1, "failed": -1, "cancelled": -1}.get(status, -1)
        if status == "failed":
            completed_through = stages.index(terminal_stage) - 1 if terminal_stage in stages else -1
        if status == "cancelled":
            completed_through = stages.index(terminal_stage) - 1 if terminal_stage in stages else -1
        if status == "refused":
            terminal_stage = "preflight"
        elif status == "clarify":
            terminal_stage = "planning"
        result["trajectory"] = [
            {"stage": stage, "outcome": "passed" if index <= completed_through else
             "blocked" if stage == terminal_stage or (status == "answered" and stage == "answer") else "not_reached"}
            for index, stage in enumerate(stages)
        ]
        result["model_budget"] = {
            "max_calls": 2,
            "calls_used": result["model_call_count"],
            "max_prompt_chars_per_call": 12_000,
            "max_completion_tokens_per_call": min(getattr(getattr(self.planner, "settings", None),
                                                            "max_completion_tokens", 1200), 1200),
        }
        return result

    def _analyze(self, question: str, *, run_id: str | None = None, cancel_event: Event | None = None,
                 authorized_region: str | None = None) -> dict:
        run_id = run_id or str(uuid.uuid4())
        denied = _preflight(question)
        if denied:
            return {"run_id": run_id, **denied}
        try:
            plan = self.planner.plan(question)
        except NeedsClarification as exc:
            return {"run_id": run_id, "status": "clarify", "message": str(exc), "choices": exc.choices}
        except PlanError as exc:
            return {"run_id": run_id, "status": "failed", "stage": "planning", "message": str(exc)}
        if plan.region is not None and plan.region not in REGION_VALUES:
            return {"run_id": run_id, "status": "failed", "stage": "planning",
                    "reason": "invalid_region_filter", "message": "地区筛选值不在已知数据目录中。"}
        if cancel_event is not None and cancel_event.is_set():
            return {"run_id": run_id, "status": "cancelled", "stage": "planning", "message": "分析已取消。"}
        if authorized_region is not None:
            if plan.region is not None and plan.region != authorized_region:
                return {"run_id": run_id, "status": "refused", "reason": "region_scope_denied", "message": "请求地区超出授权范围。"}
            plan = type(plan)(plan.metric_id, plan.start_date, plan.end_date, authorized_region, plan.dimension, plan.periods)
        metric = METRICS[plan.metric_id]
        try:
            dataset_version, data_fingerprint = read_data_identity(self.database_path)
        except QueryExecutionError as exc:
            if metric.required_data_versions:
                return {"run_id": run_id, "status": "failed", "stage": "dataset", "message": str(exc)}
            dataset_version = "unknown"
        if metric.required_data_versions and dataset_version not in metric.required_data_versions:
            return {"run_id": run_id, "status": "clarify", "stage": "dataset",
                    "message": "该指标需要企业扩展数据集；请配置 INSIGHTPILOT_DATABASE_PATH。",
                    "required_data_versions": list(metric.required_data_versions), "actual_data_version": dataset_version}
        try:
            compiled = compile_query(plan)
        except ValueError as exc:
            return {"run_id": run_id, "status": "failed", "stage": "compilation", "message": str(exc)}
        try:
            rows, data_version = execute_readonly(self.database_path, compiled, cancel_event)
        except QueryCancelledError as exc:
            return {"run_id": run_id, "status": "cancelled", "stage": "query", "message": str(exc)}
        except QueryExecutionError as exc:
            return {"run_id": run_id, "status": "failed", "stage": "query", "message": str(exc)}
        try:
            verification = verify_result(plan, rows, self.database_path, cancel_event)
        except QueryCancelledError as exc:
            return {"run_id": run_id, "status": "cancelled", "stage": "verification", "message": str(exc)}
        except QueryExecutionError as exc:
            return {"run_id": run_id, "status": "failed", "stage": "verification", "message": str(exc)}
        if not verification["passed"]:
            return {"run_id": run_id, "status": "failed", "stage": "verification",
                    "message": "独立汇总复核未通过；已停止生成结论。", "verification": verification}
        return {
            "run_id": run_id,
            "status": "answered",
            "metric": {"id": metric.metric_id, "name": metric.label, "formula": metric.formula,
                       "time_basis": metric.time_basis, "refund_status": metric.refund_status,
                       "definition_version": metric.definition_version, "effective_from": metric.effective_from,
                       "grain": metric.grain, "definition_source": metric.definition_source,
                       "lineage": list(metric.lineage)},
            "plan": {"start_date_inclusive": plan.start_date, "end_date_exclusive": plan.end_date,
                     "periods": [list(period) for period in plan.periods],
                     "region": plan.region, "dimension": plan.dimension},
            "data_version": data_version,
            "data_fingerprint": data_fingerprint,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "sql": compiled.sql,
            "parameters": list(compiled.params),
            "rows": rows,
            "visualization": _visualization(plan, rows),
            "verification": verification,
            "limitations": ["只回答语义目录支持的单指标和维度。", "当前数据为可复现的合成样本。"],
        }
