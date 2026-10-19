"""OpenAI-compatible plan extraction. The model never writes or executes SQL."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date

from .config import Settings
from .metrics import METRICS, retrieve_metrics


@dataclass(frozen=True)
class QueryPlan:
    metric_id: str
    start_date: str
    end_date: str
    region: str | None
    dimension: str | None


class PlanError(ValueError):
    pass


class NeedsClarification(ValueError):
    def __init__(self, message: str, choices: list[str] | None = None):
        super().__init__(message)
        self.choices = choices or []


class OpenAICompatiblePlanner:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _complete(self, question: str, candidates: list[str]) -> dict:
        metric_context = [asdict(METRICS[item]) for item in candidates]
        system = (
            "Extract exactly one analytics query plan as JSON. Never produce SQL. "
            "Use only provided metric_id values. Dates must be YYYY-MM-DD. Convert an inclusive final calendar date to the next date as end_date_exclusive. "
            "If question has no explicit calendar date range, no single metric, or requests multiple metrics, set clarify=true and explain briefly. "
            "Allowed dimension: null, category, channel. Allowed region: a literal from user or null. "
            'Return keys: clarify (boolean), clarification (string), metric_id, start_date, end_date_exclusive, region, dimension.'
        )
        body = {
            "model": self.settings.model,
            "temperature": 0,
            "max_tokens": self.settings.max_completion_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"question": question, "candidate_metrics": metric_context}, ensure_ascii=False)},
            ],
        }
        request = urllib.request.Request(
            self.settings.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.settings.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("response is not an object")
            return result
        except urllib.error.HTTPError as exc:
            # Do not include response body: it may echo sensitive request data.
            raise PlanError(f"LLM request failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PlanError(f"LLM request failed: {type(exc).__name__}") from None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise PlanError("LLM returned an invalid structured response") from None

    def plan(self, question: str) -> QueryPlan:
        q = question.strip()
        if not q:
            raise NeedsClarification("请提供分析问题。")
        if re.search(r"上周|本周|昨天|今天|最近\s*\d+\s*天", q):
            raise NeedsClarification("请提供明确的起止日期；S1 不猜测相对时间的业务时区。")
        if "退款率" in q and any(term in q for term in ("贡献", "贡献度")):
            raise NeedsClarification("S1 一次处理一个指标；请先分别询问退款率或品类贡献度。")
        candidates = retrieve_metrics(q)
        if "退款率" in q and not any(term in q for term in ("订单率", "订单数", "金额", "实付")):
            raise NeedsClarification("请选退款订单率或退款金额率。", ["approved_refund_order_rate", "approved_refund_amount_rate"])
        if not candidates:
            raise NeedsClarification("未识别到 S1 支持的退款率或退款金额贡献指标。")
        result = self._complete(q, [metric.metric_id for metric in candidates])
        if result.get("clarify") is True:
            raise NeedsClarification(str(result.get("clarification") or "请补充指标或时间范围。"))
        metric_id = result.get("metric_id")
        if metric_id not in METRICS or metric_id not in {metric.metric_id for metric in candidates}:
            raise PlanError("LLM selected a metric outside the retrieved candidates")
        try:
            start = date.fromisoformat(result["start_date"])
            end = date.fromisoformat(result["end_date_exclusive"])
        except (KeyError, TypeError, ValueError):
            raise NeedsClarification("请提供可解析的起止日期。") from None
        if start >= end:
            raise NeedsClarification("结束日期必须晚于开始日期。")
        dimension = result.get("dimension")
        if dimension not in (None, "category", "channel"):
            raise PlanError("LLM selected a dimension outside the allowed set")
        region = result.get("region")
        if region is not None and (not isinstance(region, str) or len(region) > 30):
            raise PlanError("LLM returned an invalid region filter")
        return QueryPlan(metric_id, start.isoformat(), end.isoformat(), region, dimension)
