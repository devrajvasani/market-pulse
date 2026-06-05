"""MarketPulse — LocalStack console (read-only dashboard).

Queries the LocalStack emulator with boto3 server-side (so the browser never has to deal
with SigV4 signing or CORS) and serves a small AWS-console-like dashboard: service health,
S3 buckets + objects, Lambda, Secrets Manager (names only), KMS, EventBridge, and more.

Read-only by design: it only ever LISTS resources and NEVER reads a secret value.
Dev tool only — dummy credentials against the local emulator.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib

import boto3
import requests
from flask import Flask, jsonify

ENDPOINT = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
_INDEX = (pathlib.Path(__file__).parent / "index.html").read_text(encoding="utf-8")

# Dummy creds + region come from the container environment (set in docker-compose).
_session = boto3.session.Session()
app = Flask(__name__)


def _client(service: str):
    return _session.client(service, endpoint_url=ENDPOINT, region_name=REGION)


def _iso(value):
    return value.isoformat() if isinstance(value, dt.datetime) else value


def _collect(payload: dict, key: str, fn) -> None:
    """Run one service collector into payload[key]; record any failure in payload['errors']."""
    try:
        payload[key] = fn()
    except Exception as exc:  # one dead/disabled service must never break the whole dashboard
        payload[key] = []
        payload["errors"][key] = str(exc).splitlines()[0][:200]


def _s3_buckets():
    s3 = _client("s3")
    buckets = []
    for b in s3.list_buckets().get("Buckets", []):
        objects = []
        try:
            contents = s3.list_objects_v2(Bucket=b["Name"]).get("Contents", [])
        except Exception as exc:
            contents = []
            objects.append({"key": f"(error: {str(exc).splitlines()[0][:80]})", "size": 0})
        for o in contents:
            objects.append(
                {"key": o["Key"], "size": o["Size"], "modified": _iso(o.get("LastModified"))}
            )
        buckets.append(
            {"name": b["Name"], "created": _iso(b.get("CreationDate")), "objects": objects}
        )
    return buckets


def _lambdas():
    fns = _client("lambda").list_functions().get("Functions", [])
    return [
        {
            "name": f["FunctionName"],
            "runtime": f.get("Runtime"),
            "memory": f.get("MemorySize"),
            "timeout": f.get("Timeout"),
            "layers": len(f.get("Layers") or []),
            "modified": f.get("LastModified"),
        }
        for f in fns
    ]


def _secrets():
    # Names + metadata only — the dashboard must NEVER read a secret value.
    secs = _client("secretsmanager").list_secrets().get("SecretList", [])
    return [{"name": s["Name"], "modified": _iso(s.get("LastChangedDate"))} for s in secs]


def _kms_keys():
    kms = _client("kms")
    aliases: dict[str, list[str]] = {}
    for a in kms.list_aliases().get("Aliases", []):
        if a.get("TargetKeyId"):
            aliases.setdefault(a["TargetKeyId"], []).append(a["AliasName"])
    return [
        {"id": k["KeyId"], "aliases": aliases.get(k["KeyId"], [])}
        for k in kms.list_keys().get("Keys", [])
    ]


def _rules():
    rules = _client("events").list_rules().get("Rules", [])
    return [
        {"name": r["Name"], "schedule": r.get("ScheduleExpression"), "state": r.get("State")}
        for r in rules
    ]


def _log_groups():
    groups = _client("logs").describe_log_groups().get("logGroups", [])
    return [{"name": g["logGroupName"], "retention": g.get("retentionInDays")} for g in groups]


def _sqs():
    urls = _client("sqs").list_queues().get("QueueUrls") or []
    return [u.rsplit("/", 1)[-1] for u in urls]


def _sns():
    topics = _client("sns").list_topics().get("Topics") or []
    return [t["TopicArn"].rsplit(":", 1)[-1] for t in topics]


def _state_machines():
    machines = _client("stepfunctions").list_state_machines().get("stateMachines", [])
    return [m["name"] for m in machines]


def _kinesis():
    return _client("kinesis").list_streams().get("StreamNames", [])


def _iam_roles():
    return [r["RoleName"] for r in _client("iam").list_roles().get("Roles", [])]


def gather() -> dict:
    """Collect a read-only snapshot of every LocalStack service the dashboard shows."""
    payload: dict = {
        "meta": {
            "endpoint": ENDPOINT,
            "region": REGION,
            "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        },
        "errors": {},
    }
    try:
        health = requests.get(f"{ENDPOINT}/_localstack/health", timeout=5).json()
        payload["health"] = health.get("services", {})
        payload["meta"]["edition"] = health.get("edition")
        payload["meta"]["version"] = health.get("version")
    except Exception as exc:
        payload["health"] = {}
        payload["errors"]["health"] = str(exc).splitlines()[0][:200]
    try:
        payload["meta"]["account"] = _client("sts").get_caller_identity()["Account"]
    except Exception:
        payload["meta"]["account"] = "-"

    _collect(payload, "s3", _s3_buckets)
    _collect(payload, "lambda", _lambdas)
    _collect(payload, "secrets", _secrets)
    _collect(payload, "kms", _kms_keys)
    _collect(payload, "events", _rules)
    _collect(payload, "logs", _log_groups)
    _collect(payload, "sqs", _sqs)
    _collect(payload, "sns", _sns)
    _collect(payload, "stepfunctions", _state_machines)
    _collect(payload, "kinesis", _kinesis)
    _collect(payload, "iam", _iam_roles)
    return payload


@app.get("/api/data")
def api_data():
    """Return the current LocalStack snapshot as JSON (polled by the dashboard)."""
    return jsonify(gather())


@app.get("/healthz")
def healthz():
    """Liveness probe for the dashboard container itself."""
    return {"ok": True}


@app.get("/")
def index():
    """Serve the dashboard shell (it renders client-side from /api/data)."""
    return _INDEX


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
