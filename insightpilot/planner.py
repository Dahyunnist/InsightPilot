"""OpenAI-compatible plan extraction. The model never writes or executes SQL."""

from __future__ import annotations

import json
import re
import time
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
    periods: tuple[tuple[str, str], ...] = ()


class PlanError(ValueError):
    pass


class NeedsClarification(ValueError):
    def __init__(self, message: str, choices: list[str] | None = None):
        super().__init__(message)
        self.choices = choices or []


class OpenAICompatiblePlanner:
    def __init__(self, settings: Settings, *, use_metric_retrieval: bool = True):
        self.settings = settings
        self.use_metric_retrieval = use_metric_retrieval
        self.last_call_usage: dict | None = None
        self.last_call_latency_ms: float | None = None

    def _complete(self, question: str, candidates: list[str], allowed_metric_ids: list[str]) -> dict:
        metric_context = [asdict(METRICS[item]) for item in candidates]
        system = (
            "Extract exactly one analytics query plan as JSON. Never produce SQL. "
            "Choose metric_id only from allowed_metric_ids. Dates must be YYYY-MM-DD. "
            "Convert each inclusive final calendar date to the next date as exclusive end. "
            "For comparison, return start_date, end_date_exclusive and comparison_periods as a list of two [start, exclusive_end] ranges. "
            "Return the shortest valid JSON. If question has no explicit calendar date(s), no single metric, requests multiple metrics, or cannot be grounded, set clarify=true and explain briefly. "
            "Allowed dimensions come from candidate_metrics.valid_dimensions. Allowed region: a literal from user or null. "
            'Return keys: clarify (boolean), clarification (string), metric_id, start_date, end_date_exclusive, comparison_periods, region, dimension.'
        )
        body = {
            "model": self.settings.model,
            "temperature": 0,
            "max_tokens": self.settings.max_completion_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"question": question, "candidate_metrics": metric_context,
                                                            "allowed_metric_ids": allowed_metric_ids}, ensure_ascii=False)},
            ],
        }
        request = urllib.request.Request(
            self.settings.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.settings.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        self.last_call_usage = None
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self.last_call_usage = {
                    key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if isinstance(usage.get(key), int)
                }
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
        finally:
            self.last_call_latency_ms = round((time.perf_counter() - started) * 1000, 3)

    def plan(self, question: str) -> QueryPlan:
        q = question.strip()
        if not q:
            raise NeedsClarification("请提供分析问题。")
        if re.search(r"截至.{0,12}(?:当时|当日).{0,12}(?:可见|入库|记录)|(?:当时|当日).{0,12}(?:可见|入库|记录)", q):
            raise NeedsClarification("当前数据没有历史入库快照；请改问退款发生时间或当前口径。")
        if (any(term in q for term in ("各地区", "按地区", "地区维度", "地区")) and "渠道" in q):
            raise NeedsClarification("当前一次只支持一个分组维度；请先选地区或渠道。")
        if sum(region in q for region in ("华东", "华南", "华北", "西南")) > 1:
            raise NeedsClarification("当前一次只支持一个地区筛选；请分开查询。")
        if re.search(r"上周|本周|昨天|今天|最近\s*\d+\s*天", q):
            raise NeedsClarification("请提供明确的起止日期；S1 不猜测相对时间的业务时区。")
        if "退款率" in q and any(term in q for term in ("贡献", "贡献度")):
            raise NeedsClarification("S1 一次处理一个指标；请先分别询问退款率或品类贡献度。")
        date_strings = re.findall(r"\d{4}-\d{2}-\d{2}", q)
        source_periods: tuple[tuple[str, str], ...] = ()
        try:
            parsed_days = [date.fromisoformat(value) for value in date_strings]
            if len(parsed_days) == 1 and any(term in q for term in ("当天", "当日")):
                source_periods = ((parsed_days[0].isoformat(), date.fromordinal(parsed_days[0].toordinal() + 1).isoformat()),)
            elif len(parsed_days) == 2:
                source_periods = ((parsed_days[0].isoformat(), date.fromordinal(parsed_days[1].toordinal() + 1).isoformat()),)
            elif len(parsed_days) == 4:
                source_periods = tuple(
                    (parsed_days[i].isoformat(), date.fromordinal(parsed_days[i + 1].toordinal() + 1).isoformat())
                    for i in (0, 2)
                )
        except ValueError:
            raise NeedsClarification("日期无效，请提供正确的日历日期。") from None
        if not source_periods:
            raise NeedsClarification("请提供明确日期；对比问题需提供两个日期区间。")
        use_metric_retrieval = getattr(self, "use_metric_retrieval", True)
        candidates = retrieve_metrics(q) if use_metric_retrieval else []
        if "退款率" in q and not any(term in q for term in ("订单率", "订单数", "金额", "实付")):
            raise NeedsClarification("请选退款订单率或退款金额率。", ["approved_refund_order_rate", "approved_refund_amount_rate"])
        if use_metric_retrieval and not candidates:
            raise NeedsClarification("未识别到 S1 支持的退款率或退款金额贡献指标。")
        candidate_ids = [metric.metric_id for metric in candidates]
        allowed_metric_ids = candidate_ids if use_metric_retrieval else list(METRICS)
        try:
            result = self._complete(q, candidate_ids, allowed_metric_ids)
        except PlanError as exc:
            if "invalid structured response" not in str(exc):
                raise
            result = self._complete(q, candidate_ids, allowed_metric_ids)
        if result.get("clarify") is True:
            raise NeedsClarification(str(result.get("clarification") or "请补充指标或时间范围。"))
        metric_id = result.get("metric_id")
        if metric_id not in allowed_metric_ids:
            raise PlanError("LLM selected a metric outside the retrieved candidates")
        start, end = (date.fromisoformat(value) for value in source_periods[0])
        if any(date.fromisoformat(period_start) >= date.fromisoformat(period_end) for period_start, period_end in source_periods):
            raise NeedsClarification("结束日期必须晚于开始日期。")
        dimension = result.get("dimension")
        if dimension not in (None, "category", "channel", "region", "month_utc", "refund_day_utc", "refund_status", "campaign"):
            raise PlanError("LLM selected a dimension outside the allowed set")
        if dimension is not None and dimension not in METRICS[metric_id].valid_dimensions:
            raise PlanError("LLM selected a dimension outside the metric's semantic contract")
        region = result.get("region")
        if region is not None and (not isinstance(region, str) or len(region) > 30):
            raise PlanError("LLM returned an invalid region filter")
        return QueryPlan(metric_id, start.isoformat(), end.isoformat(), region, dimension, source_periods)
