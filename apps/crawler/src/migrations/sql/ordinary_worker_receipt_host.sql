-- Failure-associated runtime routing travels with the canonical receipt.
-- Publish it only with token-guarded Redis settlement, never into an inflight
-- board snapshot. A completed receipt retains it through process loss/reaping.
ALTER TABLE public.ordinary_worker_write_fence
    ADD COLUMN learned_egress_host text,
    ADD CONSTRAINT ordinary_receipt_host_valid CHECK (
        learned_egress_host IS NULL OR (
            task_kind = 'monitor'
            AND state = 'completed'
            AND octet_length(learned_egress_host) BETWEEN 1 AND 253
            AND learned_egress_host !~ '[[:cntrl:]|]'
        )
    );
