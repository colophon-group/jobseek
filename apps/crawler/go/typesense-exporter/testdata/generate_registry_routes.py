"""Freeze registered routing facts and actual Python board preparation outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

from src import sync
from src.core import monitors, scrapers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--boards", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    contract = {
        "api_monitors": sorted(sync._API_MONITOR_TYPES),
        "scrapers": {name: entry.needs_browser for name, entry in scrapers._REGISTRY.items()},
        "render_scrapers": sorted(scrapers._RENDER_AWARE_SCRAPERS),
    }
    output = json.dumps(contract, indent=2, sort_keys=True) + "\n"
    target = Path(__file__).resolve().parents[1] / "registry_routes.json"
    if args.check:
        assert target.read_text() == output, "Go registry routing contract changed"
    else:
        target.write_text(output)
    if args.boards:
        rows = list(pl.read_csv(args.boards, infer_schema_length=0).iter_rows(named=True))
    else:
        rows = []
        configs = [None, {}, {"api_url": "https://fixture.invalid/api"}, {"render": True}]
        for name in sorted(monitors.all_monitor_types() | scrapers.all_scraper_types()):
            for config in configs:
                rows.append(
                    {
                        "board_url": "https://fixture.invalid/careers",
                        "monitor_type": name,
                        "scraper_type": name,
                        "monitor_config": json.dumps(config) if config is not None else None,
                        "scraper_config": json.dumps(config) if config is not None else None,
                    }
                )
        for raw in [
            "[]",
            "null",
            "42",
            "broken",
            "{} {}",
            '{"unicode":"ä🚀","float":1e-8,"big":123456789012345678901234567890}',
            '{"actions":[],"render":0,"source":{},"browser_expression":{}}',
            '{"actions":["click"],"render":{},"source":"browser"}',
            '{"fallback":{"type":"json-ld","config":{"render":true}}}',
            '{"fallback":{"type":"api_sniffer","config":{"api_url":"https://fixture.invalid/api","fallback":{"type":"dom","config":{"render":true}}}}}',
            '{"fallback":{"type":"unknown","config":{"fallback":{"type":"dom","config":{"render":true}}}}}',
            '{"fallback":{"type":1,"config":[]}}',
            '{"identity_migration":{"approved":true},"_identity_migration_receipt":{},"_monitor_config_fingerprint":"old"}',
        ]:
            rows.append(
                {
                    "board_url": "https://fixture.invalid/ä",
                    "monitor_type": "nextdata",
                    "scraper_type": "unknown",
                    "monitor_config": raw,
                    "scraper_config": raw,
                }
            )
    if rows:
        cases = []
        for row in rows:
            case = {"row": row}
            try:
                metadata = json.loads(row.get("monitor_config") or "{}")
                if not isinstance(metadata, dict):
                    raise ValueError("monitor_config")
                if row.get("scraper_type"):
                    metadata["scraper_type"] = row["scraper_type"]
                if row.get("scraper_config"):
                    config = json.loads(row["scraper_config"])
                    if not isinstance(config, dict):
                        raise ValueError("scraper_config")
                    metadata["scraper_config"] = config
                metadata["_monitor_config_fingerprint"] = sync._monitor_config_fingerprint(
                    row["board_url"], row["monitor_type"], metadata
                )
                case.update(
                    metadata=metadata,
                    monitor_browser=sync.monitor_needs_browser(row["monitor_type"], metadata),
                    scraper_browser=sync._scraper_chain_needs_browser(
                        metadata.get("scraper_type"), metadata.get("scraper_config")
                    ),
                    throttle_key=sync._compute_throttle_key(
                        row["monitor_type"], row["board_url"], metadata
                    ),
                )
            except (ValueError, TypeError):
                case["invalid"] = True
            cases.append(case)
        target = args.out or Path(__file__).with_name("registry_board_fixture.json")
        output = json.dumps(cases, indent=2, ensure_ascii=False) + "\n"
        if args.check:
            assert target.read_text() == output, "Go registry board fixture changed"
        else:
            target.write_text(output)


if __name__ == "__main__":
    main()
