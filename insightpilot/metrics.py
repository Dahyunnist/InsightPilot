"""Small explicit metric catalog used as the planner's ground truth."""

from __future__ import annotations

from dataclasses import dataclass


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
}


def retrieve_metrics(question: str) -> list[Metric]:
    """Rank metrics by explicit business-language cues; never pick vague refund rate."""
    q = question.lower()
    scores = {key: 0 for key in METRICS}
    if "按下单日期" in q and "退款订单数" in q:
        scores["approved_refund_cohort_summary"] = 10
    if any(term in q for term in ("每天", "按退款日期", "退款事件日")):
        scores["approved_refund_event_amount"] = 10
    if any(term in q for term in ("退款状态", "待处理金额", "批准和待处理", "按状态")):
        scores["refund_amount_by_status"] = 10
    if any(term in q for term in ("已支付订单和批准退款订单", "订单数和批准退款", "退款订单数量")):
        scores["approved_refund_order_counts"] = 10
    if any(term in q for term in ("贡献", "占比", "贡献度")):
        scores["approved_refund_amount_contribution"] += 5
    if any(term in q for term in ("金额", "实付", "销售额")):
        scores["approved_refund_amount_rate"] += 4
    if any(term in q for term in ("订单率", "订单数", "多少单")):
        scores["approved_refund_order_rate"] += 4
    if "批准退款金额" in q and "退款率" not in q and not any(term in q for term in ("贡献", "占比")):
        scores["approved_refund_amount"] += 6
    if "退款率" in q:
        scores["approved_refund_order_rate"] += 1
        scores["approved_refund_amount_rate"] += 1
    best = max(scores.values(), default=0)
    ordered = sorted(key for key, score in scores.items() if score == best and score > 0)
    return [METRICS[key] for key in ordered]


def catalog_document() -> dict:
    """Return a deterministic, versioned semantic catalog for clients and audit."""
    from dataclasses import asdict

    return {
        "catalog_version": "insightpilot-semantic-catalog-v1",
        "data_contract_version": "insightpilot-s0-v1",
        "metrics": [asdict(METRICS[key]) for key in sorted(METRICS)],
        "dimensions": {
            "category": {"source": "products.category", "grain": "product", "sensitivity": "internal"},
            "channel": {"source": "orders.channel", "grain": "order", "sensitivity": "internal"},
            "refund_day_utc": {"source": "refunds.refunded_at", "grain": "UTC day", "sensitivity": "internal"},
            "refund_status": {"source": "refunds.status", "grain": "refund record", "sensitivity": "internal"},
        },
        "excluded_definitions": {
            "refund_event_rate": "未有冻结公式、分母或生效版本；禁止自动替换成订单率/金额率。"
        },
    }
