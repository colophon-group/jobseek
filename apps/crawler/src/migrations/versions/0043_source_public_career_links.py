"""Record approved public career-page links separately from crawler endpoints.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE public.source_coverage_public_link (
    board_id uuid PRIMARY KEY REFERENCES public.job_board(id) ON DELETE CASCADE,
    company_id uuid NOT NULL REFERENCES public.company(id),
    public_url text NOT NULL CHECK (length(public_url) BETWEEN 9 AND 2048),
    provenance text NOT NULL CHECK (provenance IN
        ('employer-careers', 'ats-careers', 'reviewed-redirect')),
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION public.jobseek_source_coverage_public_link_revision()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.board_id IS DISTINCT FROM NEW.board_id THEN
            RAISE EXCEPTION 'approved public link board identity cannot change';
        END IF;
    END IF;
    UPDATE public.source_coverage_scope
    SET revision = gen_random_uuid(), tracked_since = clock_timestamp(),
        effective_config_sha256 = NULL
    WHERE board_id = COALESCE(NEW.board_id, OLD.board_id);
    RETURN NULL;
END;
$$;
CREATE TRIGGER source_coverage_public_link_revision
AFTER INSERT OR UPDATE OR DELETE ON public.source_coverage_public_link
FOR EACH ROW EXECUTE FUNCTION public.jobseek_source_coverage_public_link_revision();
""")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER source_coverage_public_link_revision ON public.source_coverage_public_link"
    )
    op.execute("DROP FUNCTION public.jobseek_source_coverage_public_link_revision()")
    op.execute("DROP TABLE public.source_coverage_public_link")
