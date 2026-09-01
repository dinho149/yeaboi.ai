"""AWS — CloudWatch alarms, under a credential yeaboi bounds.

The recommended path is ``sts:AssumeRole`` against a role the customer creates,
with an external ID and — the part that matters — a **session policy yeaboi
passes on every assume call**. Effective permission is the intersection of the
role's own policy and that document, so even a customer who pastes an admin
role ARN gets a read-only session. The guarantee is enforced by code here, not
by a paragraph in a setup guide.

The ambient chain is used only to *call* AssumeRole; none of its permissions
reach the session. Ambient-as-the-identity stays available and warned, because
yeaboi cannot describe what that identity may do.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from yeaboi.connectors.spec import AuthMethod, Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_title, iso, parse_ts

logger = logging.getLogger(__name__)

#: The session policy every assume call carries. Read-only by construction, and
#: asserted as such by tests/unit/test_connectors_auth.py — a write verb added
#: here fails the build rather than shipping.
READ_ONLY_SESSION_POLICY: dict = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Resource": "*",
            "Action": ["cloudwatch:DescribeAlarms", "cloudwatch:DescribeAlarmHistory"],
        }
    ],
}

#: How long an assumed session lives. One gather, not one working day.
SESSION_SECONDS = 900
SESSION_NAME = "yeaboi-read-only"

_ASSUME_ROLE = "assume_role"
_AMBIENT = "ambient"

CONNECTOR = Connector(
    key="aws",
    label="AWS",
    family="cloud",
    section="connections",
    summary="CloudWatch alarms that fired while the sprint ran",
    detail=(
        "yeaboi assumes a role you create and passes a read-only session policy on every "
        "call, so the session can only describe CloudWatch alarms however broad the role "
        "itself is. It never reads logs, metric series, billing or any other service, never "
        "writes anything back — not even acknowledging an alarm — and never attributes an "
        "alarm to a person."
    ),
    verify="_verify_aws",
    fetch="fetch",
    docs_url="https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-user_externalid.html",
    glyph="\U0001f7e7",  # 🟧 — brand orange
    accent="rgb(255,153,0)",
    auth_env="AWS_AUTH_METHOD",
    auth_methods=(
        AuthMethod(
            key=_ASSUME_ROLE,
            label="Assume a role",
            summary="yeaboi assumes a role you create, and narrows the session to read-only itself.",
            recommended=True,
            setup_url="https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-user_externalid.html",
            envs=("AWS_ROLE_ARN", "AWS_EXTERNAL_ID"),
        ),
        AuthMethod(
            key=_AMBIENT,
            label="This machine's credentials",
            summary="Use whatever ~/.aws/credentials, an env var or an instance role already provides.",
            warning="yeaboi cannot bound what this identity may do — it runs as whatever this machine is.",
            envs=(),
        ),
    ),
    fields=(
        ConnectorField(
            env="AWS_AUTH_METHOD",
            label="How to connect",
            choices=(_ASSUME_ROLE, _AMBIENT),
            default=_ASSUME_ROLE,
            env_arg="auth_method",
            hint="Assuming a role is the only one yeaboi can bound",
        ),
        ConnectorField(
            env="AWS_ROLE_ARN",
            label="Role ARN",
            auth_method=_ASSUME_ROLE,
            env_arg="role_arn",
            placeholder="arn:aws:iam::123456789012:role/yeaboi-read-only",
            help_url="https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-user_externalid.html",
            help_scope="Trust yeaboi's caller and require the external ID below; no policy needed",
        ),
        ConnectorField(
            env="AWS_EXTERNAL_ID",
            label="External ID",
            secret=True,
            auth_method=_ASSUME_ROLE,
            env_arg="external_id",
            hint="Generated for you — paste it into the role's trust policy",
        ),
        ConnectorField(
            env="AWS_CLOUD_REGION",
            label="Region",
            env_arg="region",
            default="us-east-1",
            placeholder="eu-west-1",
            hint="Which region's CloudWatch alarms to read",
        ),
    ),
)

#: The message a probe returns when the extra is not installed. Matches the
#: Ollama shape above it in provider_verification: an actionable failure, not
#: "No module named 'boto3'" from a generic handler.
PKG_MISSING = "AWS support isn't installed — run: uv sync --extra cloud (or: pip install 'yeaboi[cloud]'), then retry"


def installed() -> bool:
    """Whether boto3 is importable, without importing it."""
    import importlib.util

    return importlib.util.find_spec("boto3") is not None


def new_external_id() -> str:
    """A fresh external ID for a role's trust policy.

    Generated locally so the customer never has to invent one, and so the value
    is unguessable — an external ID is what stops a confused deputy assuming a
    role on somebody else's behalf.
    """
    import secrets

    return f"yeaboi-{secrets.token_urlsafe(24)}"


def client(service: str):
    """A boto3 client for ``service``, under the chosen auth method.

    Under ``assume_role`` the ambient chain is used only to call AssumeRole; the
    returned client carries the assumed session's credentials, intersected with
    ``READ_ONLY_SESSION_POLICY``. Under ``ambient`` it carries whatever the
    machine has, which is the thing that method's warning names.
    """
    import boto3

    from yeaboi.connectors.fetching import env

    region = env("AWS_CLOUD_REGION", "us-east-1")
    if env("AWS_AUTH_METHOD", _ASSUME_ROLE) != _ASSUME_ROLE:
        logger.info("aws: using ambient credentials (unbounded by yeaboi)")
        return boto3.client(service, region_name=region)

    sts = boto3.client("sts", region_name=region)
    assumed = sts.assume_role(
        RoleArn=env("AWS_ROLE_ARN"),
        RoleSessionName=SESSION_NAME,
        ExternalId=env("AWS_EXTERNAL_ID"),
        DurationSeconds=SESSION_SECONDS,
        Policy=json.dumps(READ_ONLY_SESSION_POLICY),
    )
    creds = assumed["Credentials"]
    logger.info("aws: assumed a read-only session in %s", region)
    return boto3.client(
        service,
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """CloudWatch alarms that entered ALARM during the window.

    ``DescribeAlarmHistory``, not ``DescribeAlarms``: the latter is a snapshot of
    what is firing now, and a report on a finished sprint needs what fired then.
    ``HistoryData`` and ``HistorySummary`` carry the metric values that tripped
    the alarm and are deliberately never read — the alarm's NAME is the event.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, FetchError, env

    if not installed():
        raise FetchError(PKG_MISSING)

    try:
        history = client("cloudwatch").describe_alarm_history(
            HistoryItemType="StateUpdate",
            StartDate=window_start,
            EndDate=window_end,
            MaxRecords=PAGE_LIMIT,
        )
    except Exception as exc:
        from yeaboi.provider_verification import _connection_error

        raise FetchError(_connection_error(exc)) from None

    region = env("AWS_CLOUD_REGION", "us-east-1")
    events = []
    for row in history.get("AlarmHistoryItems") or []:
        if not isinstance(row, dict):
            continue
        # The summary is the only field that says which way the alarm moved, and
        # it is a fixed AWS sentence rather than customer content.
        summary = str(row.get("HistorySummary") or "")
        if "to ALARM" not in summary:
            continue
        name = str(row.get("AlarmName") or "")
        stamp = parse_ts(str(row.get("Timestamp") or ""))
        events.append(
            OpsEvent(
                kind="alert",
                source="aws",
                ref=f"{name}@{iso(stamp)}",
                title=clean_title(name),
                status="firing",
                started_at=iso(stamp),
                url=f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#alarmsV2:",
            )
        )
    return tuple(events)
