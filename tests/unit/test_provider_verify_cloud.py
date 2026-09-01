"""The three cloud probes — what each failure actually tells the user.

A probe that reports every failure as "invalid credentials" sends the user to
re-cut the wrong thing, so each of these asserts the diagnosis rather than the
boolean.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from yeaboi import provider_verification as pv


@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch):
    from yeaboi.connectors import registry

    for env in registry.all_envs():
        monkeypatch.delenv(env, raising=False)


def _fake_boto3(monkeypatch, *, caller=None, assume=None, alarms=None, raises=None):
    """Inject a boto3 that answers the three calls the probe makes.

    Faked through sys.modules rather than installed: the cloud extra stays out
    of the dev dependencies, and one test can then assert the missing-extra
    message honestly.
    """
    module = types.ModuleType("boto3")

    def client(service, **kw):
        if raises:
            raise raises
        stub = MagicMock()
        stub.get_caller_identity.return_value = caller or {"Arn": "arn:aws:iam::1:user/dev"}
        stub.assume_role.return_value = assume or {
            "Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}
        }
        stub.describe_alarms.return_value = alarms if alarms is not None else {"MetricAlarms": []}
        return stub

    module.client = client
    monkeypatch.setitem(sys.modules, "boto3", module)
    return module


class TestAws:
    def test_a_missing_extra_says_how_to_install_it(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.aws.installed", lambda: False)
        ok, message = pv._verify_aws(auth_method="ambient")
        assert ok is False
        assert "uv sync --extra cloud" in message

    def test_assuming_a_role_needs_both_halves(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.aws.installed", lambda: True)
        ok, message = pv._verify_aws(auth_method="assume_role", role_arn="arn:aws:iam::1:role/r")
        assert ok is False and "external ID" in message

    def test_ambient_reports_whose_identity_it_became(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.aws.installed", lambda: True)
        _fake_boto3(monkeypatch, caller={"Arn": "arn:aws:iam::1:user/admin"})
        ok, message = pv._verify_aws(auth_method="ambient", region="eu-west-1")
        assert ok is True
        # The whole point of offering it: the user sees what they pointed at.
        assert "arn:aws:iam::1:user/admin" in message
        assert "cannot bound" in message

    def test_assuming_a_role_names_the_role_and_the_session(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.aws.installed", lambda: True)
        _fake_boto3(monkeypatch)
        monkeypatch.setenv("AWS_AUTH_METHOD", "assume_role")
        monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::1:role/yeaboi-read-only")
        monkeypatch.setenv("AWS_EXTERNAL_ID", "yeaboi-xyz")
        ok, message = pv._verify_aws(
            auth_method="assume_role", role_arn="arn:aws:iam::1:role/yeaboi-read-only", external_id="yeaboi-xyz"
        )
        assert ok is True
        assert "yeaboi-read-only" in message and "read-only session" in message

    def test_the_session_policy_travels_on_the_assume_call(self, monkeypatch):
        # The guarantee, end to end: not that the policy exists, but that this
        # code path is what hands it to STS.
        import json

        from yeaboi.connectors import aws

        seen = {}
        module = types.ModuleType("boto3")

        def client(service, **kw):
            stub = MagicMock()
            stub.assume_role.side_effect = lambda **call: (
                seen.update(call) or {"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}}
            )
            return stub

        module.client = client
        monkeypatch.setitem(sys.modules, "boto3", module)
        monkeypatch.setenv("AWS_AUTH_METHOD", "assume_role")
        monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::1:role/admin-role")
        monkeypatch.setenv("AWS_EXTERNAL_ID", "yeaboi-xyz")
        aws.client("cloudwatch")
        assert json.loads(seen["Policy"]) == aws.READ_ONLY_SESSION_POLICY
        assert seen["ExternalId"] == "yeaboi-xyz"

    def test_ambient_passes_no_session_policy(self, monkeypatch):
        # It cannot: there is no assume call to hang one on. That is exactly
        # what the method's warning is about.
        from yeaboi.connectors import aws

        calls = []
        module = types.ModuleType("boto3")
        module.client = lambda service, **kw: calls.append((service, kw)) or MagicMock()
        monkeypatch.setitem(sys.modules, "boto3", module)
        monkeypatch.setenv("AWS_AUTH_METHOD", "ambient")
        aws.client("cloudwatch")
        assert calls == [("cloudwatch", {"region_name": "us-east-1"})]

    def test_a_transport_failure_is_redacted(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.aws.installed", lambda: True)
        _fake_boto3(monkeypatch, raises=RuntimeError("boom AKIAIOSFODNN7EXAMPLE"))
        ok, message = pv._verify_aws(auth_method="ambient")
        assert ok is False and "AKIAIOSFODNN7EXAMPLE" not in message


def _fake_google(monkeypatch, token="tok"):
    google = types.ModuleType("google")
    auth = types.ModuleType("google.auth")
    imp = types.ModuleType("google.auth.impersonated_credentials")
    transport = types.ModuleType("google.auth.transport")
    requests = types.ModuleType("google.auth.transport.requests")

    creds = MagicMock()
    creds.token = token
    auth.default = lambda: (creds, "project")
    imp.Credentials = MagicMock(return_value=creds)
    requests.Request = MagicMock()
    google.auth = auth
    auth.impersonated_credentials = imp
    auth.transport = transport
    transport.requests = requests
    for name, mod in (
        ("google", google),
        ("google.auth", auth),
        ("google.auth.impersonated_credentials", imp),
        ("google.auth.transport", transport),
        ("google.auth.transport.requests", requests),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return imp


class TestGcp:
    def test_a_missing_extra_says_how_to_install_it(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: False)
        ok, message = pv._verify_gcp(auth_method="impersonate", project_id="p")
        assert ok is False and "uv sync --extra cloud" in message

    def test_it_needs_a_project(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
        ok, message = pv._verify_gcp(auth_method="impersonate")
        assert ok is False and "project id" in message

    def test_impersonation_needs_an_account_to_impersonate(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
        ok, message = pv._verify_gcp(auth_method="impersonate", project_id="p")
        assert ok is False and "service account" in message

    def test_only_read_only_scopes_are_requested(self, monkeypatch):
        from yeaboi.connectors import gcp

        imp = _fake_google(monkeypatch)
        monkeypatch.setenv("GCP_AUTH_METHOD", "impersonate")
        monkeypatch.setenv("GCP_SERVICE_ACCOUNT", "reader@p.iam.gserviceaccount.com")
        gcp.access_token()
        assert imp.Credentials.call_args.kwargs["target_scopes"] == list(gcp.SCOPES)
        assert imp.Credentials.call_args.kwargs["target_principal"] == "reader@p.iam.gserviceaccount.com"

    def test_a_refused_token_blames_the_role_not_the_credential(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.gcp.access_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.connectors.http.probe_status", lambda url, **kw: (403, ""))
        ok, message = pv._verify_gcp(
            auth_method="impersonate", project_id="p", service_account="r@p.iam.gserviceaccount.com"
        )
        assert ok is False and "errorreporting.viewer" in message

    def test_success_names_the_identity(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.gcp.access_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.connectors.http.probe_status", lambda url, **kw: (200, ""))
        ok, message = pv._verify_gcp(
            auth_method="impersonate", project_id="proj", service_account="r@p.iam.gserviceaccount.com"
        )
        assert ok is True and "proj" in message and "r@p.iam.gserviceaccount.com" in message

    def test_ambient_says_it_cannot_be_bounded(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.gcp.access_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.connectors.http.probe_status", lambda url, **kw: (200, ""))
        ok, message = pv._verify_gcp(auth_method="ambient", project_id="proj")
        assert ok is True and "cannot bound" in message


class TestAzure:
    def test_it_names_every_missing_field_at_once(self):
        ok, message = pv._verify_azure_cloud(tenant_id="t")
        assert ok is False
        for expected in ("client id", "client secret", "subscription id"):
            assert expected in message

    def test_a_rejected_app_registration_is_not_a_missing_role(self, monkeypatch):
        from yeaboi.connectors.fetching import FetchError

        def boom():
            raise FetchError("credentials rejected — re-run `yeaboi connections verify azure_cloud`")

        monkeypatch.setattr("yeaboi.connectors.azure_cloud.access_token", boom)
        ok, message = pv._verify_azure_cloud(tenant_id="t", client_id="c", client_secret="s", subscription_id="sub")
        assert ok is False and "credentials rejected" in message

    def test_a_missing_role_assignment_says_which_role(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.azure_cloud.access_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.connectors.http.probe_status", lambda url, **kw: (403, ""))
        ok, message = pv._verify_azure_cloud(tenant_id="t", client_id="c", client_secret="s", subscription_id="sub")
        assert ok is False and "Monitoring Reader" in message

    def test_the_token_exchange_goes_through_the_url_guard(self, monkeypatch):
        # The one POST in the layer. If it stopped going through post_form it
        # would stop being checked, silently.
        from yeaboi.connectors import azure_cloud

        seen = {}

        def fake_post(url, *, data, timeout=10):
            seen["url"] = url
            seen["scope"] = data["scope"]
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"access_token": "tok"}
            return resp

        monkeypatch.setattr("yeaboi.connectors.http.post_form", fake_post)
        monkeypatch.setenv("AZURE_CLOUD_TENANT_ID", "tenant-1")
        monkeypatch.setenv("AZURE_CLOUD_CLIENT_ID", "client-1")
        monkeypatch.setenv("AZURE_CLOUD_CLIENT_SECRET", "secret-1")
        assert azure_cloud.access_token() == "tok"
        assert seen["url"].startswith("https://login.microsoftonline.com/tenant-1/")
        assert seen["scope"] == "https://management.azure.com/.default"

    def test_success_states_the_bound_it_actually_has(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.azure_cloud.access_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.connectors.http.probe_status", lambda url, **kw: (200, ""))
        ok, message = pv._verify_azure_cloud(tenant_id="t", client_id="c", client_secret="s", subscription_id="sub")
        assert ok is True and "Monitoring Reader" in message
