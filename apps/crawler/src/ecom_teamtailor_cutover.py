"""Forward/reverse hooks for the bounded ECOM Teamtailor identity cutover."""

from __future__ import annotations

import importlib
from typing import cast

import asyncpg

_CUTOVER_STATE = """
SELECT board.id,
       board.metadata -> '_identity_migration_receipt' AS receipt
FROM job_board AS board
JOIN company ON company.id = board.company_id
WHERE company.slug = 'ecom-agroindustrial'
  AND board.board_slug = 'ecom-agroindustrial-global'
  AND board.board_url IN (
      'https://careers.ecomtrading.com/jobs',
      'https://ecomtradinggroup.teamtailor.com/jobs'
  )
  AND board.crawler_type = 'rss'
"""
_MIGRATION_ID = "ecom-teamtailor-stable-id-v1"
_MIGRATION_VERSION = 1
_CONFIG_FINGERPRINT = "f48a13a3ab31582e825441be395c03a13da8426f72e310f6e0e98765dd99af86"


def _migration_sql(*, rollback: bool = False) -> str:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )
    name = (
        "_ROLLBACK_ECOM_TEAMTAILOR_IDENTITIES"
        if rollback
        else "_MIGRATE_ECOM_TEAMTAILOR_IDENTITIES"
    )
    return cast(str, getattr(migration, name))


async def apply_ecom_teamtailor_cutover(connection: asyncpg.Connection) -> str:
    """Reapply the exact idempotent forward SQL after current config sync."""
    return await connection.execute(_migration_sql())


async def rollback_ecom_teamtailor_cutover(connection: asyncpg.Connection) -> str:
    """Restore the receipt-recorded aliases before an old runtime restarts."""
    return await connection.execute(_migration_sql(rollback=True))


async def ecom_teamtailor_cutover_state(connection: asyncpg.Connection) -> str:
    """Return a fail-closed pre-deploy state for rollback ownership."""
    rows = await connection.fetch(_CUTOVER_STATE)
    if len(rows) > 1:
        raise RuntimeError("ECOM identity cutover found ambiguous board ownership")
    if not rows:
        return "absent"
    receipt = rows[0]["receipt"]
    if receipt is None:
        return "pending"
    if isinstance(receipt, str):
        import json

        receipt = json.loads(receipt)
    if not isinstance(receipt, dict):
        raise RuntimeError("ECOM identity cutover found a malformed receipt")
    if (
        receipt.get("id") == _MIGRATION_ID
        and receipt.get("version") == _MIGRATION_VERSION
        and receipt.get("config_fingerprint") == _CONFIG_FINGERPRINT
        and isinstance(receipt.get("rollback_rows"), list)
    ):
        return "complete"
    raise RuntimeError("ECOM identity cutover found a mismatched receipt")
