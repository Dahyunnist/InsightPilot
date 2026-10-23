"""Small, fixed rule planner used as a same-case comparison baseline."""

from __future__ import annotations

import re
from datetime import date

from .metrics import METRICS, REGION_VALUES
from .planner import NeedsClarification, QueryPlan


class RulePlanner:
    last_call_usage = None
    last_call_latency_ms = None
    last_call_count = 0
    last_calls: list[dict] = []
    last_response_model = None
    last_call_created_at = None

    def plan(self, question: str) -> QueryPlan:
        q = question.strip()
        marketing = any(cue in q.lower() for cue in ("roi", "投资回报率", "投资回报"))
        if marketing:
            raw_dates = re.findall(r"\d{4}-\d{2}-\d{2}", q)
            try:
                dates = [date.fromisoformat(value) for value in raw_dates]
            except ValueError:
                raise NeedsClarification("日期无效。") from None
            if len(dates) == 1 and any(word in q for word in ("当天", "当日")):
                start, end = dates[0], date.fromordinal(dates[0].toordinal() + 1)
            elif len(dates) == 2:
                start, end = dates[0], date.fromordinal(dates[1].toordinal() + 1)
            else:
                raise NeedsClarification("规则基线需要一个明确时间窗。")
            if any(region in q for region in REGION_VALUES):
                raise NeedsClarification("营销费用没有地区分摊。")
            dimension = "campaign" if any(word in q for word in (
                "各活动", "各营销活动", "按活动", "不同活动", "逐个营销活动", "每个营销活动"
            )) else None
            return QueryPlan("marketing_net_roi", start.isoformat(), end.isoformat(), None, dimension)

        if "截至" in q and any(word in q for word in ("当时", "可见", "入库", "记录")):
            raise NeedsClarification("合成规则基线不支持历史入库快照。")
        if ("地区" in q and "渠道" in q) or sum(region in q for region in REGION_VALUES) > 1:
            raise NeedsClarification("一次只接受一个分组维度和一个地区筛选。")

        raw_dates = re.findall(r"\d{4}-\d{2}-\d{2}", q)
        try:
            dates = [date.fromisoformat(value) for value in raw_dates]
        except ValueError:
            raise NeedsClarification("日期无效。") from None
        if len(dates) == 1 and "当天" in q:
            start, end = dates[0], date.fromordinal(dates[0].toordinal() + 1)
        elif len(dates) == 2:
            start, end = dates[0], date.fromordinal(dates[1].toordinal() + 1)
        else:
            raise NeedsClarification("规则基线需要一个明确时间窗。")
        if start >= end:
            raise NeedsClarification("结束日期必须晚于开始日期。")

        if "活动" in q and any(word in q for word in ("退款金额贡献", "活动退款贡献", "活动归因")):
            if "贡献" not in q and "归因" not in q:
                raise NeedsClarification("规则基线无法确定活动归因口径。")
            return QueryPlan("approved_refund_campaign_attribution", start.isoformat(), end.isoformat(),
                             next((value for value in REGION_VALUES if value in q), None), "campaign")

        amount_cues = ("金额率", "金额退款率", "占实付", "实付金额")
        order_cues = ("订单率", "订单退款率")
        amount = any(cue in q for cue in amount_cues)
        order = any(cue in q for cue in order_cues)
        if amount == order:
            raise NeedsClarification("请选择批准退款订单率或金额率中的一个。")
        metric_id = "approved_refund_amount_rate" if amount else "approved_refund_order_rate"

        if any(word in q for word in ("品类", "商品品类")) and any(word in q for word in ("按", "各")):
            dimension = "category"
        elif "渠道" in q and any(word in q for word in ("按", "各")):
            dimension = "channel"
        elif "地区" in q and any(word in q for word in ("按", "各")):
            dimension = "region"
        elif any(word in q for word in ("按月", "每月", "月度趋势")):
            dimension = "month_utc"
        else:
            dimension = None

        if dimension is not None and dimension not in METRICS[metric_id].valid_dimensions:
            raise NeedsClarification("该指标不支持所选切片。")
        if start.isoformat() < METRICS[metric_id].effective_from:
            raise NeedsClarification("该指标尚未在所选时间窗生效。")
        region = next((value for value in REGION_VALUES if value in q), None)
        return QueryPlan(metric_id, start.isoformat(), end.isoformat(), region, dimension)
