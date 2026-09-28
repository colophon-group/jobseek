# Swisslog description locale repair

The first production ingestion created all 53 jobs and uploaded complete bodies.
Eight German and one Swedish description were indexed with the monitor's default
`en` locale. Their primary URLs therefore returned 404 despite the actual bodies
being available under `de` and `sv`. This is not an upload delay.

The runtime fix updates locales whenever description enrichment stages a body,
independently of whether title enrichment is enabled. Later monitor refreshes
preserve the detail-owned locale. The original title and other monitor fields
retain their existing ownership.

The [manifest](swisslog-locale-repair.json) records exactly nine posting IDs,
canonical source URLs, expected old/new locales, the Swisslog company ID and the
SHA-256 of each already uploaded public HTML body. No publisher refetch, new
scrape, queue change, billing change or R2 write is needed.

## Coordinated repair procedure

1. Coordinate with the active Go/Lightpanda migration owner. Follow the current
   [cold rollback/selector procedure](../../24-go-lightpanda-resumption-plan.md);
   never clear active selectors directly or bypass release attestation.
2. Deploy the reviewed locale fix through normal CI/full crawler deployment.
   Use the exact successfully promoted crawler revision and immutable image.
3. Under the documented `jobseek-maintenance window` and shared host mutation
   lock, mount the committed repair script and manifest read-only into that
   attested image. Use its normal scoped runtime environment. Run its Python:

   ```sh
   /app/.venv/bin/python /repair/repair-swisslog-description-locales.py \
     --manifest /repair/swisslog-locale-repair.json \
     --expected-crawler-revision <full-promoted-revision>
   ```

4. Review the nine dry-run proposals. Repeat the identical command with `--apply`
   only in the same coordinated maintenance window. The transaction verifies
   source, company, active state, absence of a live scrape lease, exact old
   locales, exactly one stored body, completed R2 upload and the frozen HTML
   checksum. Any mismatch aborts the whole transaction. Only `locales` and
   `updated_at` change; rerunning already-repaired rows is a no-op.
5. Let normal CDC export publish the corrections. Verify all nine first-locale
   R2 URLs return 200, all 53 Swisslog descriptions resolve, and a later board
   refresh preserves the corrected primary locales. Complete normal migration
   restoration and conservation checks with its owner.

The script defaults to a read-only transaction. It does not acquire the host
maintenance lock itself, change schedules, start containers or alter releases;
those responsibilities remain with the supported maintenance/deploy workflow.
The operation must not be run directly with uncoordinated host credentials.

Secondary detected languages do not imply separate uploaded translations:
roboa, CASCINATION and Flybotix already have correct primary body URLs. They are
not included in this repair.
