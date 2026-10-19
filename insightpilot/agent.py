"""S1 analysis state flow: preflight, retrieve, plan, compile, execute, explain."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .metrics import METRICS
from .planner import NeedsClarification, OpenAICompatiblePlanner, PlanError
from .runner import QueryExecutionError, execute_readonly
from .sql import compile_query


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
        compiled = compile_query(plan)
        try:
            rows, data_version = execute_readonly(self.database_path, compiled)
        except QueryExecutionError as exc:
            return {"run_id": run_id, "status": "failed", "stage": "query", "message": str(exc)}
        return {
            "run_id": run_id,
            "status": "answered",
            "metric": {"id": metric.metric_id, "name": metric.label, "formula": metric.formula,
                       "time_basis": metric.time_basis, "refund_status": metric.refund_status},
            "plan": {"start_date_inclusive": plan.start_date, "end_date_exclusive": plan.end_date,
                     "region": plan.region, "dimension": plan.dimension},
            "data_version": data_version,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "sql": compiled.sql,
            "parameters": list(compiled.params),
            "rows": rows,
            "limitations": ["S1 只支持单指标、明确日期区间、品类或渠道分组。", "数据为固定合成样本。"],
        }
