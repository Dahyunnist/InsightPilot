"""S1 analysis state flow: preflight, retrieve, plan, compile, execute, explain."""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .metrics import METRICS
from .planner import NeedsClarification, OpenAICompatiblePlanner, PlanError
from .runner import QueryExecutionError, execute_readonly
from .sql import compile_query
from .verification import verify_result


def _preflight(question: str) -> dict | None:
    q = question.lower()
    if re.search(r"修改|删除|新增|写入|更新|标记.*完成|改成", q):
        return {"status": "refused", "reason": "write_operation_not_supported", "message": "当前分析 Agent 只读，不能修改数据。"}
    if re.search(r"手机号|姓名|身份证|住址|家庭住址|个人信息", q):
        return {"status": "refused", "reason": "restricted_personal_data", "message": "请求涉及受限个人信息，已拒绝。"}
    return None


class InsightPilot:
    def __init__(self, planner: OpenAICompatiblePlanner, database_path: Path):
        self.planner = planner
        self.database_path = database_path

    def analyze(self, question: str) -> dict:
        started = time.perf_counter()
        if hasattr(self.planner, "last_call_usage"):
            self.planner.last_call_usage = None
        result = self._analyze(question)
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result["model_usage"] = getattr(self.planner, "last_call_usage", None)
        result["model_latency_ms"] = getattr(self.planner, "last_call_latency_ms", None)
        return result

    def _analyze(self, question: str) -> dict:
        run_id = str(uuid.uuid4())
        denied = _preflight(question)
        if denied:
            return {"run_id": run_id, **denied}
        try:
            plan = self.planner.plan(question)
        except NeedsClarification as exc:
            return {"run_id": run_id, "status": "clarify", "message": str(exc), "choices": exc.choices}
        except PlanError as exc:
            return {"run_id": run_id, "status": "failed", "stage": "planning", "message": str(exc)}
        metric = METRICS[plan.metric_id]
        try:
            compiled = compile_query(plan)
        except ValueError as exc:
            return {"run_id": run_id, "status": "failed", "stage": "compilation", "message": str(exc)}
        try:
            rows, data_version = execute_readonly(self.database_path, compiled)
        except QueryExecutionError as exc:
            return {"run_id": run_id, "status": "failed", "stage": "query", "message": str(exc)}
        try:
            verification = verify_result(plan, rows, self.database_path)
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
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "sql": compiled.sql,
            "parameters": list(compiled.params),
            "rows": rows,
            "verification": verification,
            "limitations": ["S1 只支持单指标、明确日期区间、品类或渠道分组。", "数据为固定合成样本。"],
        }
