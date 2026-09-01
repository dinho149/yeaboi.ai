"""Auth methods, and the guards that make "read-only" structural.

The claim this stage makes is that a customer who over-grants still gets a
read-only session, because yeaboi narrows the credential in code. A claim like
that is worth exactly as much as the test that fails when someone edits it, so
the read-only vocabulary is parsed and asserted here rather than trusted.
"""

from __future__ import annotations

import re

import pytest

from yeaboi.connectors import aws, azure_cloud, gcp, registry

CLOUD = [c for c in registry.all_connectors() if c.family == "cloud"]
WITH_METHODS = [c for c in registry.all_connectors() if c.auth_methods]


class TestTheReadOnlyVocabulary:
    """AWS's session policy is the guarantee. It is parsed, not read."""

    def test_it_allows_exactly_one_thing(self):
        statements = aws.READ_ONLY_SESSION_POLICY["Statement"]
        assert len(statements) == 1
        assert statements[0]["Effect"] == "Allow"

    def test_every_action_is_a_read_verb(self):
        for action in aws.READ_ONLY_SESSION_POLICY["Statement"][0]["Action"]:
            assert re.fullmatch(r"[a-z0-9]+:(Describe|Get|List)[A-Za-z]*", action), f"{action} is not a read"

    def test_no_action_is_a_wildcard(self):
        # `cloudwatch:*` reads as scoped and is not.
        for action in aws.READ_ONLY_SESSION_POLICY["Statement"][0]["Action"]:
            assert "*" not in action

    def test_it_never_inverts(self):
        # NotAction/NotResource allow everything EXCEPT a list, which is the
        # opposite shape and would quietly grant writes.
        blob = str(aws.READ_ONLY_SESSION_POLICY)
        assert "NotAction" not in blob and "NotResource" not in blob

    def test_the_policy_actually_reaches_the_assume_call(self):
        # A policy nobody passes is a comment.
        source = (aws.__file__ and open(aws.__file__, encoding="utf-8").read()) or ""
        assert "Policy=json.dumps(READ_ONLY_SESSION_POLICY)" in source

    def test_gcp_asks_only_for_read_only_scopes(self):
        for scope in gcp.SCOPES:
            assert re.search(r"\.(read|readonly|read-only)$", scope), f"{scope} is not a read-only scope"

    def test_gcp_never_offers_a_key_file(self):
        # A downloaded service-account key is a long-lived secret on disk.
        envs = {f.env for f in gcp.CONNECTOR.fields}
        assert not any("KEY" in e or "CREDENTIALS" in e for e in envs)


class TestAzureCallsOnlyGets:
    """Azure has no session policy, so its bound is that every call is a read."""

    SOURCE = open(azure_cloud.__file__, encoding="utf-8").read()

    def test_every_arm_call_is_a_get(self):
        # read_json is the GET door; a management.azure.com URL reaching any
        # other one would be a write path arriving without a conversation.
        assert "post_form" in self.SOURCE  # the token exchange, below
        posts = [ln for ln in self.SOURCE.splitlines() if "post_form(" in ln and "def " not in ln]
        assert len(posts) == 1, f"more than one POST in the module: {posts}"
        assert "token_url(" in self.SOURCE.split("post_form(", 1)[1][:120]

    def test_the_only_post_goes_to_a_fixed_microsoft_host(self):
        assert azure_cloud.LOGIN_HOST == "https://login.microsoftonline.com"
        assert azure_cloud.token_url("t").startswith(azure_cloud.LOGIN_HOST + "/")

    def test_it_asks_for_no_sdk(self):
        # azure-identity existed only for the ambient path, which is not shipped.
        assert "azure.identity" not in self.SOURCE and "azure_identity" not in self.SOURCE


class TestCloudCredentialsNeverComeFromACaller:
    @pytest.mark.parametrize("connector", CLOUD, ids=lambda c: c.key)
    def test_no_cloud_field_is_a_verify_arg(self, connector):
        # A caller-supplied role ARN paired with a STORED external ID would leak
        # that external ID into an account of the caller's choosing.
        offenders = [f.env for f in connector.fields if f.verify_arg]
        assert offenders == [], f"{connector.key} would take {offenders} from a request"

    @pytest.mark.parametrize("connector", CLOUD, ids=lambda c: c.key)
    def test_no_cloud_field_is_a_user_typed_host(self, connector):
        # There is no base_url to point anywhere, which is what covers an SDK
        # opening sockets assert_safe_url never sees.
        offenders = [f.env for f in connector.fields if f.env.endswith("_URL")]
        assert offenders == [], f"{connector.key} declares a host field {offenders}"

    @pytest.mark.parametrize("connector", CLOUD, ids=lambda c: c.key)
    def test_every_cloud_field_is_read_from_the_saved_value(self, connector):
        for field in connector.fields:
            assert field.env_arg, f"{connector.key}.{field.env} reaches its probe from nowhere"


class TestTheMethodsAreHonest:
    @pytest.mark.parametrize("connector", WITH_METHODS, ids=lambda c: c.key)
    def test_exactly_one_is_recommended(self, connector):
        assert sum(1 for m in connector.auth_methods if m.recommended) == 1

    @pytest.mark.parametrize("connector", WITH_METHODS, ids=lambda c: c.key)
    def test_every_other_method_says_why_not(self, connector):
        for method in connector.auth_methods:
            if not method.recommended:
                assert method.warning.strip(), f"{connector.key}.{method.key} is discouraged silently"

    @pytest.mark.parametrize("connector", WITH_METHODS, ids=lambda c: c.key)
    def test_the_ambient_warning_names_the_real_risk(self, connector):
        for method in connector.auth_methods:
            if method.warning:
                assert "bound" in method.warning, f"{connector.key}.{method.key} warns about nothing in particular"

    @pytest.mark.parametrize("connector", WITH_METHODS, ids=lambda c: c.key)
    def test_every_method_names_real_fields(self, connector):
        envs = {f.env for f in connector.fields}
        for method in connector.auth_methods:
            assert set(method.envs) <= envs, f"{connector.key}.{method.key} needs an env it does not declare"

    @pytest.mark.parametrize("connector", WITH_METHODS, ids=lambda c: c.key)
    def test_the_selector_is_a_required_choice_over_the_methods(self, connector):
        field = next(f for f in connector.fields if f.env == connector.auth_env)
        assert field.required, f"{connector.key} could count as connected without choosing"
        assert field.choices == tuple(m.key for m in connector.auth_methods)
        assert field.default == connector.default_method.key


class TestConnectednessFollowsTheChosenMethod:
    def test_nothing_set_is_never_connected(self, monkeypatch):
        # The §0 invariant, at its hardest point: a method that needs no
        # credential of its own must not make a connector connected by default.
        for env in registry.all_envs():
            monkeypatch.delenv(env, raising=False)
        assert registry.is_connected(aws.CONNECTOR) is False
        assert "aws" not in registry.connected()

    def test_choosing_ambient_needs_only_what_ambient_needs(self, monkeypatch):
        for env in registry.all_envs():
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setenv("AWS_AUTH_METHOD", "ambient")
        monkeypatch.setenv("AWS_CLOUD_REGION", "eu-west-1")
        assert registry.is_connected(aws.CONNECTOR) is True

    def test_the_recommended_method_still_needs_its_own_fields(self, monkeypatch):
        for env in registry.all_envs():
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setenv("AWS_AUTH_METHOD", "assume_role")
        monkeypatch.setenv("AWS_CLOUD_REGION", "eu-west-1")
        assert registry.is_connected(aws.CONNECTOR) is False
        monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::1:role/r")
        monkeypatch.setenv("AWS_EXTERNAL_ID", "yeaboi-abc")
        assert registry.is_connected(aws.CONNECTOR) is True

    def test_a_surface_can_ask_with_its_own_snapshot(self):
        # The settings screen renders from config_data, not the environment.
        # Two resolvers would be two answers.
        values = {"GCP_AUTH_METHOD": "ambient", "GCP_PROJECT_ID": "p"}
        assert registry.is_connected(gcp.CONNECTOR, values) is True
        assert registry.is_connected(gcp.CONNECTOR, {"GCP_AUTH_METHOD": "impersonate", "GCP_PROJECT_ID": "p"}) is False

    def test_an_unknown_stored_method_falls_back_to_the_recommended_one(self, monkeypatch):
        monkeypatch.setenv("AWS_AUTH_METHOD", "whatever-was-there-before")
        assert registry.chosen_method(aws.CONNECTOR).key == "assume_role"

    def test_a_connector_with_one_way_in_is_unchanged(self):
        assert registry.chosen_method(azure_cloud.CONNECTOR) is None
        assert registry.required_envs(azure_cloud.CONNECTOR) == azure_cloud.CONNECTOR.required_envs


class TestTheExternalIdIsMinted:
    def test_it_is_unguessable_and_unique(self):
        first, second = aws.new_external_id(), aws.new_external_id()
        assert first != second
        assert len(first) > 24

    def test_it_is_a_secret_everywhere_it_could_leak(self):
        from yeaboi.redaction import SECRET_ENV_KEYS
        from yeaboi.settings.engine import SECRET_ENVS

        assert "AWS_EXTERNAL_ID" in SECRET_ENVS
        assert "AWS_EXTERNAL_ID" in SECRET_ENV_KEYS


class TestThePayloadCarriesTheMethodsAndNoCredential:
    def test_the_catalog_describes_every_method(self, monkeypatch):
        from yeaboi.connectors.engine import list_connections

        monkeypatch.setenv("AWS_EXTERNAL_ID", "yeaboi-super-secret-value")
        row = next(r for r in list_connections(connected_only=False)["connectors"] if r["key"] == "aws")
        assert [m["key"] for m in row["auth_methods"]] == ["assume_role", "ambient"]
        assert row["auth_env"] == "AWS_AUTH_METHOD"
        assert "yeaboi-super-secret-value" not in str(row)

    def test_a_field_says_which_method_it_belongs_to(self):
        from yeaboi.connectors.engine import list_connections

        row = next(r for r in list_connections(connected_only=False)["connectors"] if r["key"] == "aws")
        by_env = {f["env"]: f["auth_method"] for f in row["fields"]}
        assert by_env["AWS_ROLE_ARN"] == "assume_role"
        assert by_env["AWS_CLOUD_REGION"] == ""

    def test_a_single_method_connector_sends_nothing_new(self):
        from yeaboi.connectors.engine import list_connections

        row = next(r for r in list_connections(connected_only=False)["connectors"] if r["key"] == "datadog")
        assert row["auth_methods"] == [] and row["auth_env"] == ""


class TestTheWireAllowlistIsDerived:
    def test_every_verify_field_can_actually_be_sent(self):
        # It was hand-written, and had already gone stale on Sentry's org: a
        # field the list forgot is one no caller can ever supply.
        from yeaboi.settings.engine import _connection_kinds, _verify_field_names

        needed = {name for spec in _connection_kinds().values() for name, _ in spec}
        assert needed <= set(_verify_field_names())

    def test_sentrys_org_is_carried(self):
        from yeaboi.settings.engine import _verify_field_names

        assert "org" in _verify_field_names()
