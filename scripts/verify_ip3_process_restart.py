"""Kill the synthetic Compose API mid-request and verify run-store recovery."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.ip3-restart.yml"
PROJECT = "insightpilot-ip3-restart"
BASE = "http://127.0.0.1:58081"
TOKEN = "ip3-compose-synthetic-token"
QUESTION = "2026-10-01 至 2026-10-07 批准退款订单率是多少？"
IDEMPOTENCY_KEY = "ip3-process-restart-case"
IMAGE = "insightpilot-api-probe:latest"


def compose(*args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["docker", "compose", "--project-name", PROJECT, "-f", str(COMPOSE), *args],
        cwd=ROOT, text=True, capture_output=True, timeout=timeout,
    )
    if check and result.returncode:
        raise RuntimeError(f"docker compose {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result


def api(method: str, path: str, *, payload: dict | None = None, idempotency: bool = False,
        timeout: float = 2.0) -> tuple[int, dict, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if idempotency:
        headers["Idempotency-Key"] = IDEMPOTENCY_KEY
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), {
                key.lower(): value for key, value in response.headers.items()
            }
    except urllib.error.HTTPError as response:
        return response.code, json.loads(response.read()), {
            key.lower(): value for key, value in response.headers.items()
        }


def _initial_request() -> dict:
    try:
        status, body, _headers = api("POST", "/analysis", payload={"question": QUESTION},
                                     idempotency=True, timeout=12)
        return {"http_status": status, "body": body}
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return {"transport_error": type(exc).__name__}


def _wait_health(timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if api("GET", "/health")[0] == 200:
                return
        except (OSError, TimeoutError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise TimeoutError("Compose API health did not recover within 30 seconds")


def _container_id() -> str:
    result = compose("ps", "-q")
    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(ids) != 1:
        raise RuntimeError(f"expected one probe container, found {len(ids)}")
    return ids[0]


def _image_id() -> str:
    result = subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
                            cwd=ROOT, text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError("required local image is missing; script does not pull images")
    return result.stdout.strip()


def verify() -> dict:
    started_at = time.perf_counter()
    image_id = _image_id()
    existing = compose("ps", "-q")
    if existing.stdout.strip():
        raise RuntimeError(f"Compose project {PROJECT} already has a container; refusing to touch it")

    started = False
    report = {
        "suite_id": "insightpilot-ip3-forced-process-restart",
        "image": IMAGE,
        "image_id": image_id,
        "python_runtime": "3.12.15",
        "synthetic_only": True,
        "model_calls": 0,
        "external_api_calls": 0,
        "transport": "FastAPI HTTP through localhost port mapping",
        "network": "local Compose bridge; fixed planner makes no external calls",
        "termination_signal": "SIGKILL",
        "persistent_volumes": 0,
    }
    try:
        compose("up", "-d", "--wait", "--pull", "never")
        started = True
        _wait_health()
        before_id = _container_id()

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as executor:
            request_future = executor.submit(_initial_request)
            deadline = time.monotonic() + 10
            pending = None
            while time.monotonic() < deadline:
                status, body, headers = api(
                    "POST", "/analysis", payload={"question": QUESTION}, idempotency=True,
                )
                if status == 202 and body.get("status") == "running":
                    pending = {"run_id": body.get("run_id"), "replay_header": headers.get("idempotency-replay")}
                    break
                if request_future.done():
                    raise RuntimeError("analysis completed before restart test observed running state")
                time.sleep(0.1)
            if pending is None or not pending["run_id"]:
                raise TimeoutError("did not observe an idempotent replay of the active run")

            report["pending_replay"] = {"http_status": 202, **pending}
            compose("kill", "--signal", "SIGKILL", "api-probe")
            interrupted_request = request_future.result(timeout=10)
            compose("up", "-d", "--wait", "--pull", "never")
            _wait_health()
            after_id = _container_id()

        status, record, _headers = api("GET", f"/runs/{pending['run_id']}")
        replay_status, replay_body, replay_headers = api(
            "POST", "/analysis", payload={"question": QUESTION}, idempotency=True,
        )
        report.update({
            "container_id_preserved": before_id == after_id,
            "container_id_before": before_id,
            "container_id_after": after_id,
            "inflight_request_after_kill": interrupted_request,
            "recovered_run": {"http_status": status, "status": record.get("status"),
                              "result_present": record.get("result") is not None},
            "idempotent_retry": {"http_status": replay_status, "status": replay_body.get("status"),
                                 "reason": replay_body.get("reason"),
                                 "replay_header": replay_headers.get("idempotency-replay")},
        })
        report["passed"] = bool(
            pending["replay_header"] == "true"
            and before_id == after_id
            and status == 200 and record.get("status") == "interrupted"
            and record.get("result") is None
            and replay_status == 409 and replay_body.get("status") == "interrupted"
            and replay_body.get("reason") == "result_unavailable"
            and replay_headers.get("idempotency-replay") == "true"
            and interrupted_request.get("transport_error") is not None
        )
        if not report["passed"]:
            report["failure"] = "recovered state or replay did not match interrupted-run contract"
    except Exception as exc:
        report["passed"] = False
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        if started:
            cleanup = compose("down", check=False)
            report["compose_cleanup"] = cleanup.returncode == 0
            report["cleanup_error"] = cleanup.stderr.strip() if cleanup.returncode else None
        else:
            report["compose_cleanup"] = True
    report["duration_seconds"] = round(time.perf_counter() - started_at, 3)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/ip3-process-restart-acceptance.json"))
    args = parser.parse_args()
    report = verify()
    report["output_path"] = str(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"] or not report["compose_cleanup"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
