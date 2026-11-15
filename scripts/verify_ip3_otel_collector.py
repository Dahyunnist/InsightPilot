"""Verify one synthetic API trace reaches the local Collector; never pulls images."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COMPOSE = ROOT / "docker-compose.ip3-otel-local.yml"
PROJECT = "insightpilot-ip3-otel-localtest"
COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.138.0"
OUTPUT = ROOT / "reports/ip3-otel-collector.json"
API_IMAGE = "insightpilot-api-probe:latest"
API_TOKEN = "ip3-otel-local-test-token"
QUESTION = "2026-10-05 至 2026-10-11 华东批准退款金额率是多少？ marker-sensitive-question-ip3"


def compose(*args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", "compose", "--project-name", PROJECT, "-f", str(COMPOSE),
                             *args], cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"docker compose {' '.join(args)} failed ({result.returncode})")
    return result


def _opspilot_snapshot() -> list[str]:
    result = subprocess.run(["docker", "ps", "-q", "--filter", "name=opspilot-lab-"],
                            cwd=ROOT, text=True, capture_output=True, check=True, timeout=20)
    return sorted(result.stdout.split())


def _project_resources() -> dict[str, list[str]]:
    containers = subprocess.run(["docker", "ps", "-aq", "--filter", f"name={PROJECT}-"],
                                cwd=ROOT, text=True, capture_output=True, check=True, timeout=20)
    networks = subprocess.run(["docker", "network", "ls", "-q", "--filter", f"name={PROJECT}_"],
                              cwd=ROOT, text=True, capture_output=True, check=True, timeout=20)
    return {"containers": sorted(containers.stdout.split()), "networks": sorted(networks.stdout.split())}


def _wait_api(timeout: float = 35) -> None:
    code = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2)"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = compose("exec", "-T", "api", "python", "-c", code, check=False, timeout=5)
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise TimeoutError("synthetic InsightPilot API did not become healthy")


def _wait_collector(timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = compose("logs", "--no-color", "collector", check=False, timeout=10).stdout
        if "Everything is ready. Begin running and processing data." in logs:
            return
        time.sleep(0.25)
    raise TimeoutError("local Collector did not report ready")


def _send_analysis() -> dict:
    payload = json.dumps({"question": QUESTION}, ensure_ascii=False)
    code = (
        "import json,urllib.request; "
        f"request=urllib.request.Request('http://127.0.0.1:8080/analysis', "
        f"data={payload.encode('utf-8')!r}, "
        f"headers={{'Content-Type':'application/json','Authorization':'Bearer {API_TOKEN}'}}, "
        "method='POST'); "
        "response=urllib.request.urlopen(request,timeout=20); "
        "print(json.dumps({'status':response.status,'body':json.loads(response.read())}))"
    )
    result = compose("exec", "-T", "api", "python", "-c", code, timeout=30)
    return json.loads(result.stdout.strip().splitlines()[-1])


def _image_id(image_name: str) -> str | None:
    try:
        result = subprocess.run(["docker", "image", "inspect", image_name, "--format", "{{.Id}}"],
                                cwd=ROOT, text=True, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _runtime_snapshot() -> tuple[dict | None, str | None]:
    code = (
        "import hashlib,json,os,platform,sys; from pathlib import Path; "
        "p=os.environ.get('INSIGHTPILOT_DATABASE_PATH'); "
        "print(json.dumps({'python':sys.version,'platform':platform.platform(),"
        "'dataset_sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest() if p and os.path.isfile(p) else None}))"
    )
    try:
        result = compose("exec", "-T", "api", "python", "-c", code, check=False, timeout=10)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None, None
    if result.returncode != 0:
        return None, None
    try:
        container = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return None, None
    dataset_sha = container.pop("dataset_sha256", None)
    container.update({"api_image_id": _image_id(API_IMAGE),
                      "collector_image_id": _image_id(COLLECTOR_IMAGE)})
    return container, dataset_sha


def _git_sha() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _new_report_path(base: Path, created_at_utc: str) -> Path:
    if not base.exists():
        return base
    stamp = datetime.fromisoformat(created_at_utc).strftime("%Y%m%dT%H%M%S")
    candidate = base.with_name(f"{base.stem}-run-{stamp}{base.suffix}")
    index = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.stem}-run-{stamp}-{index}{base.suffix}")
        index += 1
    return candidate


def _trace_summary(logs: str) -> dict:
    wanted = {"insightpilot.api.analysis", "insightpilot.agent.run"}
    normalized = "\n".join(re.sub(r"^[^|]*\|\s?", "", line) for line in logs.splitlines())
    blocks = re.split(r"(?=Span #\d+\s*\n)", normalized)
    spans = []
    for block in blocks:
        name_match = re.search(r"Name\s*:\s*(\S+)", block)
        if not name_match or name_match.group(1) not in wanted:
            continue
        def field(label: str) -> str | None:
            for line in block.splitlines():
                if line.strip().lower().startswith(label.lower() + " ") or \
                        line.strip().lower().startswith(label.lower() + ":"):
                    match = re.search(r"([0-9a-f]+)\s*$", line.strip(), re.IGNORECASE)
                    return match.group(1).lower() if match else None
            return None
        spans.append({"name": name_match.group(1), "trace_id": field("Trace ID"),
                      "span_id": field("ID"), "parent_id": field("Parent ID"),
                      "has_answered_status": "insightpilot.run.status: Str(answered)" in block})
    api_span = next((span for span in spans if span["name"] == "insightpilot.api.analysis"), None)
    agent_span = next((span for span in spans if span["name"] == "insightpilot.agent.run"), None)
    zero_parent = bool(api_span and (api_span["parent_id"] is None or
                                    set(api_span["parent_id"]) == {"0"}))
    parent_child = bool(api_span and agent_span and api_span["trace_id"] == agent_span["trace_id"]
                        and agent_span["parent_id"] == api_span["span_id"])
    sensitive_values = ("marker-sensitive-question-ip3", API_TOKEN, "tenant-sensitive-marker",
                        "2026-10-05", "2026-10-12", "华东")
    return {"received_span_count": len(spans), "span_names": [span["name"] for span in spans],
            "span_links": spans,
            "one_trace_id": bool(len(spans) == 2 and spans[0]["trace_id"] and
                                 len({span["trace_id"] for span in spans}) == 1),
            "api_root_and_agent_child": zero_parent and parent_child,
            "answer_status_present": bool(api_span and agent_span and api_span["has_answered_status"]
                                           and agent_span["has_answered_status"]),
            "sensitive_values_absent": not any(value in logs for value in sensitive_values)}


def main() -> None:
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
              "profile": "otel-acceptance", "collector_image": COLLECTOR_IMAGE,
              "external_model_calls": 0, "network": "isolated internal Compose network",
              "compose_cleanup": False}
    analysis_response = None
    trace_summary = None
    runtime_facts = None
    dataset_sha256 = None
    started = time.perf_counter()
    opspilot_before = _opspilot_snapshot()
    report["opspilot_container_count_before"] = len(opspilot_before)
    try:
        # Remove only stale resources from this exact acceptance project.
        compose("--profile", "otel-acceptance", "down", "--remove-orphans", check=False)
        image = subprocess.run(["docker", "image", "inspect", COLLECTOR_IMAGE], cwd=ROOT,
                               capture_output=True, text=True, timeout=20)
        if image.returncode:
            report["status"] = "blocked"
            report["blocker"] = "Local Collector image missing; no pull attempted."
        else:
            compose("--profile", "otel-acceptance", "up", "-d")
            _wait_collector()
            _wait_api()
            response = _send_analysis()
            analysis_response = response
            report["analysis_http_status"] = response["status"]
            report["analysis_status"] = response["body"].get("status")
            report["analysis_run_id"] = response["body"].get("run_id")
            report["analysis_model_call_count"] = response["body"].get("model_call_count")
            runtime_facts, dataset_sha256 = _runtime_snapshot()
            compose("stop", "api")  # Lifespan shutdown flushes the batch processor.
            logs = compose("logs", "--no-color", "collector", timeout=30).stdout
            trace_summary = _trace_summary(logs)
            report["collector"] = trace_summary
            report["status"] = "passed" if (
                response["status"] == 200 and response["body"].get("status") == "answered" and
                response["body"].get("model_call_count") == 0 and
                report["collector"]["received_span_count"] == 2 and
                report["collector"]["one_trace_id"] and report["collector"]["api_root_and_agent_child"] and
                report["collector"]["answer_status_present"] and
                report["collector"]["sensitive_values_absent"]
            ) else "failed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_class"] = type(exc).__name__
        detail = str(exc).replace(API_TOKEN, "[test token]")
        detail = detail.replace("marker-sensitive-question-ip3", "[test question]")
        report["error"] = detail[:400]
    finally:
        try:
            cleanup = compose("--profile", "otel-acceptance", "down", "--remove-orphans",
                              check=False, timeout=60)
            resources = _project_resources()
            report["compose_cleanup"] = cleanup.returncode == 0 and not any(resources.values())
            report["compose_residual_resources"] = resources
        except Exception as exc:
            report["compose_cleanup"] = False
            report["cleanup_error_class"] = type(exc).__name__
        report["opspilot_container_count_after"] = len(_opspilot_snapshot())
        report["opspilot_containers_unchanged"] = (
            opspilot_before == _opspilot_snapshot())
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        from insightpilot.run_evidence import build_ip3_otel_run_evidence
        report["run_evidence"] = build_ip3_otel_run_evidence(
            ROOT, git_sha=_git_sha(), analysis_response=analysis_response,
            trace_summary=trace_summary, runtime_facts=runtime_facts,
            dataset_sha256=dataset_sha256)
        output_path = _new_report_path(OUTPUT, report["created_at_utc"])
        report["report_path"] = str(output_path.relative_to(ROOT))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed" or not report["compose_cleanup"] or not report["opspilot_containers_unchanged"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
