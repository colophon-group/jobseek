#!/usr/bin/env python3
"""Manage only the additional web PostgreSQL firewall through the ingress adapter."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["apply", "rollback", "verify"])
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[2] / "scripts/manage-hetzner-ingress.py"
    spec = importlib.util.spec_from_file_location("jobseek_web_ingress_policy", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("reviewed ingress policy is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    client = module.HetznerClient(os.environ.get("HETZNER_API_TOKEN", ""))
    policy = module.web_postgresql_policy()
    if args.action == "rollback":
        module.rollback(client, args.state, policy=policy)
        print(json.dumps({"success": True, "web_postgresql_ingress_rolled_back": True}))
        return
    inventory = module.discover_targets(
        client, {role: os.environ.get(key, "") for role, key in module.ROLE_ENV.items()}
    )
    target = {"postgresql": inventory["postgresql"]}
    if args.action == "apply":
        result = module.apply(client, target, args.state, policy=policy)
    else:
        firewall, unrelated = module._managed_firewall(client, policy=policy)
        result = module._summary(firewall, target, unrelated, policy=policy)
    if not result["desired_rules_exact"] or not result["target_coverage_exact"]:
        raise RuntimeError("web PostgreSQL provider boundary is not exact")
    print(json.dumps({"success": True, "web_postgresql_provider_boundary": result}))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - provider errors can contain protected response data.
        print(
            json.dumps(
                {"success": False, "error": "web_postgresql_ingress_operation_failed"}
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
