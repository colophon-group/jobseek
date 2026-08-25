"""Retry hook for the bounded NW provider identity migration.

Alembic applies revision 0021 once. A failed deploy can then restore the old
Teamtailor runtime, which may re-admit legacy URLs before the next forward
attempt. The current image therefore reapplies that revision's exact
idempotent SQL after syncing the WTTJ board config and before workers restart.
"""

from __future__ import annotations

import importlib
from typing import cast

import asyncpg


def _migration_sql() -> str:
    migration = importlib.import_module(
        "src.migrations.versions.0021_migrate_nw_provider_identities"
    )
    return cast(str, migration._MIGRATE_NW_PROVIDER_IDENTITIES)


async def reapply_nw_provider_cutover(connection: asyncpg.Connection) -> str:
    """Reapply the exact revision-0021 repair after current config sync."""
    return await connection.execute(_migration_sql())
