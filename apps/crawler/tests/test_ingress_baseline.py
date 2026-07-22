"""Tests for the repo-owned Hetzner ingress and SSH baseline."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIREWALL_SCRIPT = ROOT / "scripts" / "manage-hetzner-ingress.py"
CONFORMANCE_SCRIPT = ROOT / "scripts" / "jobseek-ingress-conformance.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


firewall = _load("manage_hetzner_ingress", FIREWALL_SCRIPT)
conformance = _load("jobseek_ingress_conformance", CONFORMANCE_SCRIPT)


def test_provider_rules_are_default_deny_with_only_ssh_and_icmp() -> None:
    rules = firewall._desired_rules()

    assert [(rule["protocol"], rule.get("port")) for rule in rules] == [
        ("tcp", "22"),
        ("icmp", None),
    ]
    assert all(rule["direction"] == "in" for rule in rules)
    assert all(rule["source_ips"] == ["0.0.0.0/0", "::/0"] for rule in rules)


def test_provider_api_paths_redact_resource_ids() -> None:
    assert (
        firewall._redacted_api_path("/firewalls/123/actions/set_rules")
        == "/firewalls/{id}/actions/set_rules"
    )
    assert firewall._redacted_api_path("/actions/456") == "/actions/{id}"


def test_rule_comparison_is_order_insensitive_but_exact() -> None:
    desired = firewall._desired_rules()

    assert firewall._rules_match(list(reversed(desired)), desired)
    widened = [dict(rule) for rule in desired]
    widened[0] = {**widened[0], "port": "22-23"}
    assert not firewall._rules_match(widened, desired)


class InventoryClient:
    def __init__(self, servers) -> None:
        self.servers = servers

    def request(self, method: str, path: str):
        assert method == "GET"
        assert path == "/servers?per_page=50"
        return {"servers": self.servers}


def _server(server_id: int, name: str, address: str, private: str) -> dict:
    return {
        "id": server_id,
        "name": name,
        "public_net": {"ipv4": {"ip": address}},
        "private_net": [{"ip": private}],
    }


def test_target_discovery_is_exact_and_excludes_murmur() -> None:
    servers = [
        _server(1, "crawler", "192.0.2.1", "10.0.0.2"),
        _server(2, "postgresql", "192.0.2.2", "10.0.0.3"),
        _server(3, "typesense", "192.0.2.3", "10.0.0.4"),
        _server(4, "murmur", "192.0.2.4", "10.0.0.5"),
    ]

    targets = firewall.discover_targets(
        InventoryClient(servers),
        {
            "crawler": "192.0.2.1",
            "postgresql": "192.0.2.2",
            "typesense": "192.0.2.3",
        },
    )

    assert {role: item["id"] for role, item in targets.items()} == {
        "crawler": 1,
        "postgresql": 2,
        "typesense": 3,
    }

    with pytest.raises(firewall.IngressError, match="Murmur"):
        firewall.discover_targets(
            InventoryClient(servers),
            {
                "crawler": "192.0.2.4",
                "postgresql": "192.0.2.2",
                "typesense": "192.0.2.3",
            },
        )


def test_firewall_summary_requires_exact_target_coverage() -> None:
    targets = {
        "crawler": {"id": 1},
        "postgresql": {"id": 2},
        "typesense": {"id": 3},
    }
    managed = {
        "rules": firewall._desired_rules(),
        "applied_to": [
            {"type": "server", "server": {"id": 1}},
            {"type": "server", "server": {"id": 2}},
            {"type": "server", "server": {"id": 3}},
        ],
    }

    assert firewall._summary(managed, targets, 0)["target_coverage_exact"] is True
    managed["applied_to"].append({"type": "server", "server": {"id": 4}})
    assert firewall._summary(managed, targets, 0)["target_coverage_exact"] is False


class StatefulFirewallClient:
    def __init__(self) -> None:
        self.firewall = None

    def wait_action(self, _action) -> None:
        pass

    def request(self, method: str, path: str, *, body=None, allow_not_found=False):
        del allow_not_found
        if method == "GET" and path == "/firewalls?per_page=50":
            return {"firewalls": [self.firewall] if self.firewall else []}
        if method == "POST" and path == "/firewalls":
            self.firewall = {
                "id": 99,
                "name": body["name"],
                "labels": body["labels"],
                "rules": body["rules"],
                "applied_to": [],
            }
            return {"firewall": self.firewall, "actions": []}
        if method == "GET" and path == "/firewalls/99":
            return {"firewall": self.firewall}
        if method == "POST" and path.endswith("/actions/set_rules"):
            self.firewall["rules"] = body["rules"]
            return {"action": None}
        if method == "POST" and path.endswith("/actions/apply_to_resources"):
            self.firewall["applied_to"].extend(body["apply_to"])
            return {"action": None}
        if method == "POST" and path.endswith("/actions/remove_from_resources"):
            removed = {item["server"]["id"] for item in body["remove_from"]}
            self.firewall["applied_to"] = [
                item for item in self.firewall["applied_to"] if item["server"]["id"] not in removed
            ]
            return {"action": None}
        if method == "DELETE" and path == "/firewalls/99":
            self.firewall = None
            return {"action": None}
        raise AssertionError((method, path, body))


def test_new_provider_firewall_can_be_rolled_back_without_ids_in_output(tmp_path: Path) -> None:
    client = StatefulFirewallClient()
    state_path = tmp_path / "rollback.json"
    targets = {
        "crawler": {"id": 1},
        "postgresql": {"id": 2},
        "typesense": {"id": 3},
    }

    summary = firewall.apply(client, targets, state_path)

    assert summary == {
        "managed_firewall_present": True,
        "desired_rules_exact": True,
        "target_coverage_exact": True,
        "target_count": 3,
        "unrelated_firewall_count": 0,
    }
    assert state_path.stat().st_mode & 0o777 == 0o600
    assert "99" not in str(summary)

    firewall.rollback(client, state_path)
    assert client.firewall is None


def test_partial_provider_apply_rolls_back_automatically(tmp_path: Path, monkeypatch) -> None:
    client = StatefulFirewallClient()
    original = firewall._apply_server
    calls = 0

    def fail_second_apply(client_arg, firewall_id, server_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise firewall.IngressError("injected apply failure")
        original(client_arg, firewall_id, server_id)

    monkeypatch.setattr(firewall, "_apply_server", fail_second_apply)
    targets = {
        "crawler": {"id": 1},
        "postgresql": {"id": 2},
        "typesense": {"id": 3},
    }

    with pytest.raises(firewall.IngressError, match="injected apply failure"):
        firewall.apply(client, targets, tmp_path / "rollback.json")

    assert client.firewall is None


def test_external_verifier_rejects_any_sensitive_open_port(monkeypatch) -> None:
    targets = {
        "crawler": _server(1, "crawler", "192.0.2.1", "10.0.0.2"),
        "postgresql": _server(2, "postgresql", "192.0.2.2", "10.0.0.3"),
        "typesense": _server(3, "typesense", "192.0.2.3", "10.0.0.4"),
    }
    monkeypatch.setattr(firewall, "_probe_port", lambda _address, port: port == 22)
    assert firewall.verify_external(targets)["compliant"] is True

    monkeypatch.setattr(firewall, "_probe_port", lambda _address, port: port in {22, 5432})
    with pytest.raises(firewall.IngressError, match="external ingress"):
        firewall.verify_external(targets)


def test_listener_scope_parser_distinguishes_public_wildcard_private_and_loopback() -> None:
    parsed = conformance._listener_scopes(
        "LISTEN 0 128 0.0.0.0:9093 0.0.0.0:*\n"
        "LISTEN 0 128 127.0.0.1:9094 0.0.0.0:*\n"
        "LISTEN 0 128 10.0.0.3:5432 0.0.0.0:*\n"
        "LISTEN 0 128 8.8.8.8:8108 0.0.0.0:*\n"
    )

    assert parsed == {
        9093: {"wildcard"},
        9094: {"loopback"},
        5432: {"private"},
        8108: {"public"},
    }


def test_sshd_parser_requires_key_only_root_and_an_explicit_user_allowlist() -> None:
    state = conformance._sshd_state(
        "authenticationmethods publickey\n"
        "passwordauthentication no\n"
        "kbdinteractiveauthentication no\n"
        "pubkeyauthentication yes\n"
        "permitrootlogin without-password\n"
        "disableforwarding yes\n"
        "gatewayports no\n"
        "permittunnel no\n"
        "permituserenvironment no\n"
        "x11forwarding no\n"
        "allowusers root deploy\n"
    )
    assert state["compliant"] is True

    assert (
        conformance._sshd_state("passwordauthentication yes\nallowusers root\n")["compliant"]
        is False
    )


def test_ufw_parser_requires_only_the_role_private_service_path() -> None:
    status = "Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n"
    added = "ufw allow 22/tcp\nufw allow from 10.0.0.2 to any port 5432 proto tcp\n"

    assert conformance._ufw_state(
        status, added, "postgresql", conformance._private_address("10.0.0.2")
    )["compliant"]
    assert not conformance._ufw_state(
        status, "ufw allow 22/tcp\n", "postgresql", conformance._private_address("10.0.0.2")
    )["compliant"]


def test_postgresql_conformance_requires_exact_private_hba(monkeypatch) -> None:
    class NetworkConfig:
        @staticmethod
        def read_text(*, encoding: str) -> str:
            assert encoding == "utf-8"
            return "JOBSEEK_POSTGRES_LISTEN_ADDRESSES=127.0.0.1,10.0.0.4\n"

        @staticmethod
        def stat():
            return SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100600)

    monkeypatch.setattr(conformance, "POSTGRES_NETWORK_CONFIG", NetworkConfig())
    hba = "\n".join(
        [
            "local|||all|all|trust",
            "host|127.0.0.1|255.255.255.255|crawler|crawler|scram-sha-256",
            "host|127.0.0.1|255.255.255.255|crawler|jobseek_labeller_readonly|scram-sha-256",
            "host|::1|ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff|crawler|crawler|scram-sha-256",
            "host|::1|ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff|crawler|jobseek_labeller_readonly|scram-sha-256",
            "host|10.0.0.2|255.255.255.255|crawler|crawler|scram-sha-256",
            "host|10.0.0.2|255.255.255.255|crawler|jobseek_labeller_readonly|scram-sha-256",
        ]
    )

    def run(command):
        query = command[-1]
        if "pg_settings" in query:
            return conformance.CommandResult(
                0,
                "listen_addresses|127.0.0.1,10.0.0.4\npassword_encryption|scram-sha-256\nssl|off\n",
            )
        return conformance.CommandResult(0, hba)

    monkeypatch.setattr(conformance, "_run", run)

    state = conformance._postgres_state("container", conformance._private_address("10.0.0.3"))
    assert state["compliant"] is False

    private_hba = hba.replace("10.0.0.2", "10.0.0.3")
    monkeypatch.setattr(
        conformance,
        "_run",
        lambda command: (
            conformance.CommandResult(
                0,
                "listen_addresses|127.0.0.1,10.0.0.4\npassword_encryption|scram-sha-256\nssl|off\n",
            )
            if "pg_settings" in command[-1]
            else conformance.CommandResult(0, private_hba)
        ),
    )
    assert conformance._postgres_state("container", conformance._private_address("10.0.0.3"))[
        "compliant"
    ]


def test_network_scripts_preserve_automatic_and_future_deploy_rollback() -> None:
    host = (ROOT / "deploy/networking/install-host.sh").read_text(encoding="utf-8")
    postgres = (ROOT / "deploy/networking/harden-postgresql.sh").read_text(encoding="utf-8")
    migration = (ROOT / "deploy/backups/postgresql/migrate-container.sh").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github/workflows/deploy-hetzner-ingress.yml").read_text(encoding="utf-8")

    assert "--on-active=15m" in host
    assert "--on-active=15m" in postgres
    assert '--setenv="JOBSEEK_CRAWLER_PRIVATE_IP=' in host
    assert '--setenv="JOBSEEK_POSTGRES_PRIVATE_IP=' in postgres
    assert "root-password-was-unlocked" in host
    assert "postgres-rollback-pre-ingress" in postgres
    assert 'run_replacement "$data"' in postgres
    assert "a fresh successful PostgreSQL backup is required" in postgres
    assert "postgresql-network.env" in migration
    assert workflow.index("validate-private-paths:") < workflow.index("commit-hosts:")
    assert "rollback-after-staging-or-validation-failure:" in workflow
    assert "Immediately roll back every staged host" in workflow
    assert "Roll back any transaction left pending by a failed commit" in workflow
    assert "Audit host without writing to production" in workflow
    assert "<scripts/jobseek-ingress-conformance.py" in workflow
    assert workflow.index("commit-hosts:") < workflow.index("provider-firewall:")
