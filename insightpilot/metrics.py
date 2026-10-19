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


METRICS = {
    "approved_refund_order_rate": Metric(
        "approved_refund_order_rate", "批准退款订单率",
        "至少有一笔批准退款的已支付订单数 / 全部符合条件的已支付订单数",
        "下单时间（UTC）", "仅 approved",
    ),
    "approved_refund_amount_rate": Metric(
        "approved_refund_amount_rate", "批准退款金额率",
        "批准退款金额 / 对应订单实付金额",
        "下单时间（UTC）", "仅 approved",
    ),
    "approved_refund_amount_contribution": Metric(
        "approved_refund_amount_contribution", "批准退款金额贡献度",
        "分组批准退款金额 / 同切片全部批准退款金额",
        "下单时间（UTC）", "仅 approved",
    ),
}


def retrieve_metrics(question: str) -> list[Metric]:
    """Rank metrics by explicit business-language cues; never pick vague refund rate."""
    q = question.lower()
    scores = {key: 0 for key in METRICS}
    if any(term in q for term in ("贡献", "占比", "贡献度")):
        scores["approved_refund_amount_contribution"] += 5
    if any(term in q for term in ("金额", "实付", "销售额")):
        scores["approved_refund_amount_rate"] += 4
    if any(term in q for term in ("订单率", "订单数", "多少单")):
        scores["approved_refund_order_rate"] += 4
    if "退款率" in q:
        scores["approved_refund_order_rate"] += 1
        scores["approved_refund_amount_rate"] += 1
    best = max(scores.values(), default=0)
    ordered = sorted(key for key, score in scores.items() if score == best and score > 0)
    return [METRICS[key] for key in ordered]
