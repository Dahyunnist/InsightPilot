import hashlib

from insightpilot.ip4_evaluate import compare
from insightpilot.enterprise_evaluate import _compare as compare_enterprise


def test_failure_report_keeps_safe_query_diagnostics_without_payloads():
    sql = "SELECT private_column FROM private_table"
    result = {
        "status": "failed",
        "stage": "query",
        "reason": "QueryExecutionError",
        "message": "database error with private details",
        "sql": sql,
        "parameters": ["private-value"],
        "failure_detail": {
            "layer": "db_execution",
            "category": "statement_timeout",
            "exception_class": "QueryExecutionError",
            "backend": "postgresql",
            "tenant_context": "bound",
            "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            "parameter_count": 1,
            "tenant_id": "private-tenant",
            "raw_error": "private error detail",
        },
    }

    report = compare(
        {"case_id": "CASE-1"},
        {"domain": "refunds", "expected_status": "failed"},
        result,
    )

    assert report["actual_stage"] == "query"
    assert report["actual_reason"] == "QueryExecutionError"
    assert report["actual_failure_detail"] == {
        "layer": "db_execution",
        "category": "statement_timeout",
        "exception_class": "QueryExecutionError",
        "backend": "postgresql",
        "tenant_context": "bound",
        "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "parameter_count": 1,
    }
    serialized = repr(report)
    for private_value in (sql, "private-value", "private-tenant", "private error detail"):
        assert private_value not in serialized


def test_failure_report_drops_unstructured_reason_and_malformed_diagnostics():
    report = compare(
        {"case_id": "CASE-2"},
        {"domain": "refunds", "expected_status": "failed"},
        {"status": "failed SELECT secret", "stage": "query SELECT secret", "reason": "SQL leaked",
         "failure_detail": {"category": "bad category", "sql_sha256": "not-a-hash",
                            "parameter_count": True, "sql": "SELECT secret"}},
    )

    assert report["actual_stage"] is None
    assert report["actual_status"] is None
    assert report["actual_reason"] is None
    assert report["actual_failure_detail"] is None


def test_v13_enterprise_runner_comparison_also_records_safe_diagnostics():
    sql = "SELECT private_column FROM private_table"
    result = {
        "status": "failed", "stage": "query", "reason": "database_execution",
        "failure_detail": {
            "layer": "db_execution", "category": "database_execution",
            "exception_class": "QueryExecutionError", "backend": "postgresql",
            "tenant_context": "bound", "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "parameter_count": 2, "sql": sql, "parameters": ["private-value"],
        },
    }

    report = compare_enterprise(
        {"case_id": "CASE-3"},
        {"expected_status": "answered", "metric": "refund_rate", "dimension": None,
         "region": None, "expected": {}},
        result,
    )

    assert report["actual_stage"] == "query"
    assert report["actual_failure_detail"]["category"] == "database_execution"
    assert sql not in repr(report)
    assert "private-value" not in repr(report)
