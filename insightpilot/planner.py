"""OpenAI-compatible plan extraction. The model never writes or executes SQL."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from calendar import monthrange
from datetime import date

from .config import Settings
from .metrics import METRICS, REGION_VALUES, retrieve_metrics

_MAX_CALLS_PER_ANALYSIS = 2
_MAX_PROMPT_CHARS_PER_CALL = 12_000


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
    def __init__(self, settings: Settings, *, use_metric_retrieval: bool = True,
                 max_prompt_bytes_per_call: int | None = None):
        self.settings = settings
        self.use_metric_retrieval = use_metric_retrieval
        self.max_prompt_bytes_per_call = max_prompt_bytes_per_call
        self.last_call_usage: dict | None = None
        self.last_call_latency_ms: float | None = None
        self.last_call_count = 0
        self.last_calls: list[dict] = []
        self.last_response_model: str | None = None

    def _complete(self, question: str, candidates: list[str], allowed_metric_ids: list[str]) -> dict:
        if self.last_call_count >= _MAX_CALLS_PER_ANALYSIS:
            raise PlanError("analysis model-call budget reached")
        self.last_call_count += 1
        self.last_response_model = None
        self.last_call_created_at = None
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
        if len(system) + len(body["messages"][1]["content"]) > _MAX_PROMPT_CHARS_PER_CALL:
            self.last_call_count -= 1
            raise PlanError("analysis model prompt exceeds 12,000-character budget")
        if (self.max_prompt_bytes_per_call is not None and
                len(system.encode("utf-8")) + len(body["messages"][1]["content"].encode("utf-8"))
                > self.max_prompt_bytes_per_call):
            self.last_call_count -= 1
            raise PlanError("analysis model prompt exceeds configured byte budget")
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
                for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
                    if isinstance(usage.get(key), int):
                        self.last_call_usage[key] = usage[key]
            self.last_response_model = payload.get("model") if isinstance(payload.get("model"), str) else None
            created = payload.get("created")
            if isinstance(created, int):
                self.last_call_created_at = created
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
            self.last_calls.append({
                "latency_ms": self.last_call_latency_ms,
                "usage": self.last_call_usage,
                "response_model": self.last_response_model,
                "created_at": self.last_call_created_at,
            })

    def plan(self, question: str) -> QueryPlan:
        self.last_call_count = 0
        self.last_calls = []
        self.last_response_model = None
        self.last_call_created_at = None
        q = question.strip()
        if not q:
            raise NeedsClarification("请提供分析问题。")
        if any(term in q for term in ("按下单日期汇总", "按订单创建日期", "订单创建日期按日")) and any(
                term in q for term in ("退款订单数", "退款订单数量", "退款订单")):
            raise NeedsClarification("当前指标目录不支持按下单日期逐日分组；请改为整体 cohort 汇总或按已支持维度查询。")
        if "订单率" in q and "金额率" in q:
            raise NeedsClarification("S1 一次处理一个指标；请分别询问批准退款订单率或金额率。")
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
        if not date_strings:
            year_match = re.search(r"(\d{4})\s*年", q)
            year = int(year_match.group(1)) if year_match else None
            quarter = re.search(r"(\d{4})\s*年\s*第?([一二三四1-4])季度", q)
            if quarter:
                quarter_index = {"一": 1, "二": 2, "三": 3, "四": 4}.get(quarter.group(2))
                quarter_index = quarter_index or int(quarter.group(2))
                first_month = (quarter_index - 1) * 3 + 1
                last_month = first_month + 2
                date_strings = [f"{quarter.group(1)}-{first_month:02d}-01",
                                f"{quarter.group(1)}-{last_month:02d}-{monthrange(int(quarter.group(1)), last_month)[1]:02d}"]
            elif re.search(r"(\d{4})\s*年", q):
                month_matches = list(re.finditer(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?", q))
                if month_matches:
                    months = [(int(match.group(1) or year), int(match.group(2)), match.group(3))
                              for match in month_matches]
                    if len(months) == 1 and months[0][2]:
                        start_day = int(months[0][2])
                        tail = q[month_matches[0].end():]
                        end_match = re.search(r"(?:到|至|截至|[-—–~～])\s*(\d{1,2})\s*日", tail)
                        end_day = int(end_match.group(1)) if end_match else start_day
                        dates = [date(months[0][0], months[0][1], start_day),
                                 date(months[0][0], months[0][1], end_day)]
                        date_strings = [item.isoformat() for item in dates]
                    else:
                        first_year, first_month, _ = months[0]
                        last_year, last_month, _ = months[-1]
                        date_strings = [date(first_year, first_month, 1).isoformat(),
                                        date(last_year, last_month,
                                             monthrange(last_year, last_month)[1]).isoformat()]
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
        if candidates and candidates[0].metric_id == "marketing_net_roi" and any(
            region in q for region in REGION_VALUES
        ):
            raise NeedsClarification("营销费用目前没有地区分摊；请先移除地区筛选。")
        if "退款率" in q and not any(term in q for term in ("订单率", "订单数", "金额", "实付")):
            raise NeedsClarification("请选退款订单率或退款金额率。", ["approved_refund_order_rate", "approved_refund_amount_rate"])
        if use_metric_retrieval and not candidates:
            raise NeedsClarification("未识别到 S1 支持的退款率或退款金额贡献指标。")
        if candidates and all(any(period[0] < metric.effective_from for period in source_periods)
                              for metric in candidates):
            versions = ", ".join(sorted({metric.effective_from for metric in candidates}))
            raise NeedsClarification(f"所选指标定义自 {versions} 起生效；请提供该日期之后的时间窗。")
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
        if any(period[0] < METRICS[metric_id].effective_from for period in source_periods):
            raise NeedsClarification(f"指标定义自 {METRICS[metric_id].effective_from} 起生效；请提供该日期之后的时间窗。")
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
        requested_regions = [value for value in REGION_VALUES if value in q]
        if requested_regions:
            region = requested_regions[0]
            explicit_region_group = any(term in q for term in ("按地区", "各地区", "地区维度", "分地区", "每个地区"))
            if dimension == "region" and not explicit_region_group:
                dimension = None
        elif region is not None:
            raise PlanError("LLM selected a region not stated by the user")
        return QueryPlan(metric_id, start.isoformat(), end.isoformat(), region, dimension, source_periods)
