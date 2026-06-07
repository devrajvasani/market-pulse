"""MarketPulse — LocalStack console (read-only dashboard).

Queries the LocalStack emulator with boto3 server-side (so the browser never has to deal
with SigV4 signing or CORS) and serves an AWS-console-like dashboard with a service sidebar
and a detail panel: service health, S3 buckets + objects + encryption/versioning, Lambda
(config + env keys), Secrets Manager (names + metadata only), KMS, EventBridge, CloudWatch
Logs, SQS, SNS, Step Functions, Kinesis, IAM.

Read-only by design: it only ever LISTS/DESCRIBES resources and NEVER reads a secret value.
Lambda env values whose key looks sensitive are redacted. Dev tool only — dummy credentials.
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
    """ISO-format a datetime; pass through anything else."""
    return value.isoformat() if isinstance(value, dt.datetime) else value


def _iso_ms(epoch_ms):
    """ISO-format an epoch-milliseconds timestamp (CloudWatch Logs uses these)."""
    if not epoch_ms:
        return None
    return dt.datetime.fromtimestamp(epoch_ms / 1000, dt.UTC).isoformat(timespec="seconds")


def _safe(fn, default=None):
    """Call a single boto3 detail-fetch; swallow failures so one missing call never breaks a row."""
    try:
        return fn()
    except Exception:
        return default


def _is_sensitive_key(key: str) -> bool:
    """True if a Lambda env-var key likely holds a secret value (so we redact the value)."""
    low = key.lower()
    suffixes = ("_key", "_secret", "_token", "_password")
    needles = ("password", "apikey", "api_key", "access_key", "secret_access", "private_key")
    return low.endswith(suffixes) or any(s in low for s in needles)


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
        name = b["Name"]
        objects, total = [], 0
        for o in _safe(lambda n=name: s3.list_objects_v2(Bucket=n).get("Contents", []), []):
            total += o.get("Size", 0)
            objects.append(
                {
                    "key": o["Key"],
                    "size": o.get("Size", 0),
                    "modified": _iso(o.get("LastModified")),
                    "storage_class": o.get("StorageClass", "STANDARD"),
                }
            )
        versioning = _safe(lambda n=name: s3.get_bucket_versioning(Bucket=n).get("Status"))
        encryption = _safe(
            lambda n=name: s3.get_bucket_encryption(Bucket=n)["ServerSideEncryptionConfiguration"][
                "Rules"
            ][0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
        )
        region = _safe(
            lambda n=name: s3.get_bucket_location(Bucket=n).get("LocationConstraint") or "us-east-1"
        )
        buckets.append(
            {
                "name": name,
                "created": _iso(b.get("CreationDate")),
                "region": region or "us-east-1",
                "versioning": versioning or "Disabled",
                "encryption": encryption or "None",
                "object_count": len(objects),
                "total_size": total,
                "objects": objects,
            }
        )
    return buckets


def _lambdas():
    fns = _client("lambda").list_functions().get("Functions", [])
    out = []
    for f in fns:
        env = (f.get("Environment") or {}).get("Variables") or {}
        role = f.get("Role") or ""
        out.append(
            {
                "name": f["FunctionName"],
                "runtime": f.get("Runtime"),
                "handler": f.get("Handler"),
                "memory": f.get("MemorySize"),
                "timeout": f.get("Timeout"),
                "code_size": f.get("CodeSize"),
                "layers": len(f.get("Layers") or []),
                "role": role.rsplit("/", 1)[-1] if role else "-",
                "description": f.get("Description") or "",
                "modified": f.get("LastModified"),
                "env": {k: ("••• redacted" if _is_sensitive_key(k) else v) for k, v in env.items()},
            }
        )
    return out


def _secrets():
    # Names + metadata only — the dashboard must NEVER read a secret value.
    secs = _client("secretsmanager").list_secrets().get("SecretList", [])
    return [
        {
            "name": s["Name"],
            "description": s.get("Description") or "",
            "created": _iso(s.get("CreatedDate")),
            "modified": _iso(s.get("LastChangedDate")),
            "kms_key_id": (s.get("KmsKeyId") or "").rsplit("/", 1)[-1] or "(default)",
            "rotation_enabled": s.get("RotationEnabled", False),
        }
        for s in secs
    ]


def _kms_keys():
    kms = _client("kms")
    aliases: dict[str, list[str]] = {}
    for a in _safe(lambda: kms.list_aliases().get("Aliases", []), []):
        if a.get("TargetKeyId"):
            aliases.setdefault(a["TargetKeyId"], []).append(a["AliasName"])
    out = []
    for k in kms.list_keys().get("Keys", []):
        kid = k["KeyId"]
        meta = _safe(lambda i=kid: kms.describe_key(KeyId=i)["KeyMetadata"], {}) or {}
        rotation = _safe(
            lambda i=kid: kms.get_key_rotation_status(KeyId=i).get("KeyRotationEnabled")
        )
        out.append(
            {
                "id": kid,
                "aliases": aliases.get(kid, []),
                "state": meta.get("KeyState"),
                "key_spec": meta.get("KeySpec") or meta.get("CustomerMasterKeySpec"),
                "key_usage": meta.get("KeyUsage"),
                "rotation_enabled": rotation,
                "description": meta.get("Description") or "",
                "created": _iso(meta.get("CreationDate")),
            }
        )
    return out


def _rules():
    ev = _client("events")
    out = []
    for r in ev.list_rules().get("Rules", []):
        name = r["Name"]
        targets = _safe(lambda n=name: ev.list_targets_by_rule(Rule=n).get("Targets", []), [])
        out.append(
            {
                "name": name,
                "schedule": r.get("ScheduleExpression"),
                "state": r.get("State"),
                "description": r.get("Description") or "",
                "arn": r.get("Arn"),
                "targets": [{"id": t.get("Id"), "arn": t.get("Arn")} for t in targets],
            }
        )
    return out


def _log_groups():
    logs = _client("logs")
    out = []
    for g in logs.describe_log_groups().get("logGroups", []):
        name = g["logGroupName"]
        streams = _safe(
            lambda n=name: len(
                logs.describe_log_streams(logGroupName=n, limit=50).get("logStreams", [])
            )
        )
        out.append(
            {
                "name": name,
                "retention": g.get("retentionInDays"),
                "stored_bytes": g.get("storedBytes", 0),
                "created": _iso_ms(g.get("creationTime")),
                "streams": streams,
            }
        )
    return out


def _sqs():
    sqs = _client("sqs")
    out = []
    for url in sqs.list_queues().get("QueueUrls") or []:
        attrs = (
            _safe(
                lambda u=url: sqs.get_queue_attributes(QueueUrl=u, AttributeNames=["All"]).get(
                    "Attributes", {}
                ),
                {},
            )
            or {}
        )
        out.append(
            {
                "name": url.rsplit("/", 1)[-1],
                "url": url,
                "messages_available": attrs.get("ApproximateNumberOfMessages"),
                "messages_in_flight": attrs.get("ApproximateNumberOfMessagesNotVisible"),
            }
        )
    return out


def _sns():
    sns = _client("sns")
    out = []
    for t in sns.list_topics().get("Topics") or []:
        arn = t["TopicArn"]
        subs = _safe(
            lambda a=arn: len(sns.list_subscriptions_by_topic(TopicArn=a).get("Subscriptions", []))
        )
        out.append({"name": arn.rsplit(":", 1)[-1], "arn": arn, "subscriptions": subs})
    return out


def _state_machines():
    machines = _client("stepfunctions").list_state_machines().get("stateMachines", [])
    return [
        {
            "name": m["name"],
            "arn": m["stateMachineArn"],
            "type": m.get("type"),
            "created": _iso(m.get("creationDate")),
        }
        for m in machines
    ]


def _kinesis():
    k = _client("kinesis")
    out = []
    for name in k.list_streams().get("StreamNames", []):
        summary = (
            _safe(
                lambda n=name: k.describe_stream_summary(StreamName=n).get(
                    "StreamDescriptionSummary", {}
                ),
                {},
            )
            or {}
        )
        out.append(
            {
                "name": name,
                "status": summary.get("StreamStatus"),
                "shards": summary.get("OpenShardCount"),
                "retention_hours": summary.get("RetentionPeriodHours"),
            }
        )
    return out


def _iam_roles():
    iam = _client("iam")
    out = []
    for r in iam.list_roles().get("Roles", []):
        name = r["RoleName"]
        inline = _safe(
            lambda n=name: len(iam.list_role_policies(RoleName=n).get("PolicyNames", []))
        )
        attached = _safe(
            lambda n=name: len(
                iam.list_attached_role_policies(RoleName=n).get("AttachedPolicies", [])
            )
        )
        out.append(
            {
                "name": name,
                "arn": r.get("Arn"),
                "created": _iso(r.get("CreateDate")),
                "description": r.get("Description") or "",
                "inline_policies": inline,
                "attached_policies": attached,
            }
        )
    return out


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
