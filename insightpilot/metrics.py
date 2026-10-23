"""Small explicit metric catalog used as the planner's ground truth."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

REGION_VALUES = ("华东", "华南", "华北", "西南")


@dataclass(frozen=True)
class Metric:
    metric_id: str
    label: str
    formula: str
    time_basis: str
    refund_status: str
    definition_version: str = "1.0.0"
    effective_from: str = "2026-10-01"
    grain: str = "订单 cohort"
    source_tables: tuple[str, ...] = ("orders", "refunds")
    lineage: tuple[str, ...] = ("orders.order_id", "refunds.order_id", "refunds.amount_cents", "refunds.status")
    valid_dimensions: tuple[str, ...] = ("category", "channel")
    access_scopes: tuple[str, ...] = ("refund:read",)
    definition_source: str = "docs/product-contract.md#指标字典与口径"
    example_question: str = ""
    required_data_versions: tuple[str, ...] = ()
    business_domain: str = "退款分析"
    numerator_definition: str = ""
    denominator_definition: str = ""
    timezone: str = "UTC"
    freshness: str = "固定数据契约；无生产 freshness SLA 或实时性保证"


METRICS = {
    "approved_refund_order_rate": Metric(
        "approved_refund_order_rate", "批准退款订单率",
        "至少有一笔批准退款的已支付订单数 / 全部符合条件的已支付订单数",
        "下单时间（UTC）", "仅 approved", example_question="指定日期内批准退款订单占已支付订单比例？",
    ),
    "approved_refund_amount_rate": Metric(
        "approved_refund_amount_rate", "批准退款金额率",
        "批准退款金额 / 对应订单实付金额",
        "下单时间（UTC）", "仅 approved", example_question="指定日期内批准退款金额占实付金额比例？",
    ),
    "approved_refund_amount_contribution": Metric(
        "approved_refund_amount_contribution", "批准退款金额贡献度",
        "分组批准退款金额 / 同切片全部批准退款金额",
        "下单时间（UTC）", "仅 approved", example_question="各品类退款金额贡献度？",
    ),
    "approved_refund_amount": Metric(
        "approved_refund_amount", "批准退款金额",
        "所选时间窗口内的批准退款金额之和",
        "按计划选择下单时间或退款事件时间", "仅 approved", example_question="指定 cohort 的批准退款金额？",
    ),
    "approved_refund_event_amount": Metric(
        "approved_refund_event_amount", "按退款事件日统计的批准退款金额",
        "退款事件日的批准退款金额之和",
        "退款发生时间（UTC）", "仅 approved", grain="退款事件日", valid_dimensions=("refund_day_utc",),
        example_question="每天发生的批准退款金额？",
    ),
    "refund_amount_by_status": Metric(
        "refund_amount_by_status", "各处理状态退款金额",
        "按退款状态汇总退款记录金额；批准指标只计 approved",
        "退款发生时间（UTC）", "分状态展示", grain="退款记录状态", valid_dimensions=("refund_status",),
        example_question="指定时间窗内各退款状态金额？",
    ),
    "approved_refund_order_counts": Metric(
        "approved_refund_order_counts", "已支付与批准退款订单数",
        "已支付订单数；有至少一笔批准退款的去重订单数",
        "下单时间（UTC）", "仅 approved 订单计退款订单", valid_dimensions=(),
        example_question="已支付订单数和退款订单数？",
    ),
    "approved_refund_cohort_summary": Metric(
        "approved_refund_cohort_summary", "批准退款 cohort 汇总",
        "批准退款金额、去重退款订单数和已支付订单数",
        "下单时间（UTC）", "仅 approved", valid_dimensions=(),
        example_question="按下单日期汇总退款金额和去重订单数？",
    ),
    "approved_refund_campaign_attribution": Metric(
        "approved_refund_campaign_attribution", "批准退款金额多触点活动归因",
        "订单批准退款金额按有效 campaign touch 权重分摊；缺失 attribution 单独列示",
        "下单时间（UTC）", "仅 approved", grain="订单 × campaign attribution",
        source_tables=("orders", "refunds", "campaign_attributions", "campaigns"),
        lineage=("orders.order_id", "refunds.amount_cents", "refunds.status",
                 "campaign_attributions.weight", "campaigns.campaign_name"),
        valid_dimensions=("campaign",), definition_source="docs/product-contract.md#多触点活动归因",
        example_question="比较各活动带来的批准退款金额贡献？",
        required_data_versions=("insightpilot-enterprise-v1", "insightpilot-multidomain-v1"),
    ),
    "marketing_net_roi": Metric(
        "marketing_net_roi", "营销净投资回报率",
        "(按触点权重分摊的订单净收入 - 去重后活动费用) / 去重后活动费用",
        "订单下单时间与活动费用发生时间（UTC）；退款按发生时间并以数据快照截断",
        "批准退款扣除已记录撤销；费用采用同业务事件最新修订",
        definition_version="1.0.0", effective_from="2026-01-01",
        grain="campaign × UTC 期间", source_tables=("orders", "refunds", "refund_reversals",
                                                     "campaign_attributions", "campaigns",
                                                     "campaign_spend_events", "data_meta"),
        lineage=("orders.paid_amount_cents", "refunds.amount_cents", "refund_reversals.amount_cents",
                 "campaign_attributions.weight", "campaign_spend_events.spend_cents",
                 "campaign_spend_events.business_event_id", "campaign_spend_events.revision"),
        valid_dimensions=("campaign",), access_scopes=("marketing:read",),
        definition_source="docs/product-contract.md#营销净投资回报率",
        example_question="指定日期内各营销活动的净投资回报率是多少？",
        required_data_versions=("insightpilot-multidomain-v1",), business_domain="营销 ROI",
        numerator_definition="按 campaign touch 权重分摊的 paid_amount_cents，减批准退款，再减有效 spend_cents",
        denominator_definition="spent_at 落在请求 UTC 区间、recorded_at 不晚于数据快照，按 business_event_id 取最新修订后的费用",
        timezone="UTC",
        freshness="使用 data_meta.snapshot_at；晚于快照入库的退款撤销或费用修订不计入",
    ),
}

_COHORT_DIMS = ("category", "channel", "region", "month_utc")
for _key in ("approved_refund_order_rate", "approved_refund_amount_rate",
             "approved_refund_amount_contribution", "approved_refund_amount"):
    METRICS[_key] = replace(METRICS[_key], valid_dimensions=_COHORT_DIMS)


def retrieve_metrics(question: str) -> list[Metric]:
    """Rank metrics by explicit business-language cues; never pick vague refund rate."""
    q = question.lower()
    scores = {key: 0 for key in METRICS}
    if any(term in q for term in ("roi", "投资回报率", "广告回报率")):
        scores["marketing_net_roi"] = 20
    if any(term in q for term in ("按下单日期", "按订单创建日期", "订单创建日期按日")) and "退款订单数" in q:
        scores["approved_refund_cohort_summary"] = 10
    if any(term in q for term in ("campaign", "活动归因", "营销活动", "投放活动", "广告活动")):
        scores["approved_refund_campaign_attribution"] = 12
    if any(term in q for term in ("每天", "逐日", "按退款日期", "退款日期", "退款事件日", "退款事件日期",
                                  "退款发生日", "退款发生时间")):
        scores["approved_refund_event_amount"] = 10
    if any(term in q for term in ("退款状态", "退款处理状态", "退款处理结果", "待处理金额", "批准和待处理", "按状态")):
        scores["refund_amount_by_status"] = 10
    if any(term in q for term in ("已支付订单和批准退款订单", "已付款订单和发生批准退款的订单",
                                  "订单数和批准退款", "退款订单数量")):
        scores["approved_refund_order_counts"] = 10
    elif "订单数" in q and "退款" in q and any(term in q for term in ("已支付", "已付款", "付款成功")):
        scores["approved_refund_order_counts"] = 10
    if any(term in q for term in ("贡献", "占比", "贡献度", "份额")):
        scores["approved_refund_amount_contribution"] += 5
    if any(term in q for term in ("金额率", "金额退款率", "实付", "实收金额", "销售额", "金额之比")) or ("金额" in q and "退款率" in q):
        scores["approved_refund_amount_rate"] += 4
    if any(term in q for term in ("订单率", "订单比例", "订单占比", "订单数", "多少单")):
        scores["approved_refund_order_rate"] += 4
    elif "订单" in q and any(term in q for term in ("比例", "占比", "比率")):
        scores["approved_refund_order_rate"] += 4
    if any(term in q for term in ("退款日期", "退款发生日期", "实际退款日期")):
        scores["approved_refund_event_amount"] += 8
    if "批准退款金额" in q and not any(term in q for term in
                                         ("退款率", "金额率", "占实付", "实付金额", "比例", "比率")) and not any(
                                             term in q for term in ("贡献", "占比")):
        scores["approved_refund_amount"] += 6
    if "退款率" in q or "退款比例" in q:
        scores["approved_refund_order_rate"] += 1
        scores["approved_refund_amount_rate"] += 1
    best = max(scores.values(), default=0)
    ordered = sorted(key for key, score in scores.items() if score == best and score > 0)
    return [METRICS[key] for key in ordered]


def required_access_scopes(question: str) -> set[str]:
    candidates = retrieve_metrics(question)
    if not candidates:
        return {"refund:read"}
    return {scope for metric in candidates for scope in metric.access_scopes}


def catalog_document() -> dict:
    """Return a deterministic, versioned semantic catalog for clients and audit."""
    return {
        "catalog_version": "insightpilot-semantic-catalog-v2",
        "data_contract_version": "insightpilot-s0-v1",
        "data_contract_versions": ["insightpilot-s0-v1", "insightpilot-enterprise-v1", "insightpilot-multidomain-v1"],
        "metrics": [
            {
                **asdict(METRICS[key]),
                "numerator_definition": METRICS[key].numerator_definition or METRICS[key].formula.split("/")[0].strip(),
                "denominator_definition": METRICS[key].denominator_definition or (
                    METRICS[key].formula.split("/", 1)[1].strip()
                    if "/" in METRICS[key].formula else "不适用；该指标为金额或计数聚合"
                ),
            }
            for key in sorted(METRICS)
        ],
        "dimensions": {
            "category": {"source": "products.category", "grain": "product", "sensitivity": "internal"},
            "channel": {"source": "orders.channel", "grain": "order", "sensitivity": "internal"},
            "refund_day_utc": {"source": "refunds.refunded_at", "grain": "UTC day", "sensitivity": "internal"},
            "refund_status": {"source": "refunds.status", "grain": "refund record", "sensitivity": "internal"},
            "region": {"source": "orders.region", "grain": "order", "sensitivity": "internal",
                       "values": list(REGION_VALUES)},
            "month_utc": {"source": "orders.ordered_at", "grain": "UTC month", "sensitivity": "internal"},
            "campaign": {"source": "campaigns.campaign_name", "grain": "weighted attribution", "sensitivity": "internal"},
        },
        "excluded_definitions": {
            "refund_event_rate": "未有冻结公式、分母或生效版本；禁止自动替换成订单率/金额率。"
        },
    }
