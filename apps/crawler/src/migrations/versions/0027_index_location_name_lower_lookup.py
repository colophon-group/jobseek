"""Index case-insensitive location-name backfill lookups.

The location resolver batches cache misses by normalized name. Without a
matching expression index, every batch scans the complete ``location_name``
table and can amplify database contention across otherwise unrelated worker
stages. Include ``location_id`` so PostgreSQL can satisfy the lookup from the
index when visibility permits.

Created CONCURRENTLY so deploys do not block live taxonomy readers or writers.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-14
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_location_name_lower_lookup"


def upgrade() -> None:
    # The crawler migration chain can be exercised against an empty database
    # before the separately bootstrapped taxonomy tables exist. In installed
    # environments ``crawler sync`` owns the idempotent eventual-install
    # contract, so a fresh schema must remain migratable here.
    table = (
        op.get_bind()
        .exec_driver_sql("SELECT to_regclass('public.location_name')")
        .scalar_one_or_none()
    )
    if table is None:
        return

    with op.get_context().autocommit_block():
        # A canceled CREATE INDEX CONCURRENTLY leaves an invalid same-name
        # relation behind. Remove either that artifact or an operator-created
        # collision so a retry always installs this revision's exact shape.
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        op.execute(
            "CREATE INDEX CONCURRENTLY "
            f"{INDEX_NAME} ON location_name (lower(name)) INCLUDE (location_id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
