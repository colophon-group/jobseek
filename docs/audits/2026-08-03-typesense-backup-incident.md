# Typesense Backup Authorization Incident — 2026-08-03

Tracking issue: [#6120](https://github.com/colophon-group/jobseek/issues/6120)

## Impact and independence

The daily application-consistent Typesense backup failed with HTTP 401 from
2026-07-24 through the 2026-08-03 recovery. Its last prior success was
2026-07-23 02:10 UTC. The installed root-only backup credential no longer
authorized even authenticated read-only `GET /stats.json`, while the timer
continued attempting snapshots.

This failure predates and is independent of the leaderless runtime incident
in #6119. Process health, API readiness, backup authorization, backup
execution, and restore validity were evaluated as separate controls. No API
key, repository credential, host address, or document body is recorded here.

## Recovery

The operator created a new generated backup consumer key and first proved it
against `GET /stats.json`. Typesense 27.1 still rejects narrower generated
snapshot scopes, so the key retains the documented wildcard exception and is
confined to the root-owned backup service. The candidate was installed with
mode `0600` by atomic rename, the prior file was retained for rollback, and
the protected GitHub `production` environment secret was updated.

At 2026-08-03 10:33:32 UTC, the rotated credential started a real production
backup. It completed at 10:36:05 UTC in 152.700 seconds:

| Evidence | Value |
|---|---:|
| Typesense snapshot bytes | 1,085,124,108 |
| Encrypted Restic snapshot | `cefeaef4` |
| Repository timestamp | 2026-08-03 10:33:43 UTC |
| Retained matching snapshots after prune | 4 |
| Configured retention | 14 daily and 4 weekly |
| Destination | encrypted, off-host Restic SFTP repository |
| Timer | enabled and active |
| Latest service result | success |

The backup path also completed its retention prune and full `restic check`.
The atomic root-owned status file recorded the fresh success. Existing backup
key material is never printed or committed.

## Isolated restore evidence

The strengthened `b0f02e26` checkpoint embedded stable before/after snapshot
inventory and was restored on the separate crawler host,
never beside production. The drill used a temporary root-only repository
credential, an ephemeral API key, a unique data directory, Docker bridge
networking, and a loopback-only `127.0.0.1:18108` publication. It did not
connect to workers, the web app, or Cloudflare.

The restored artifact returned all 1,081,624,158 bytes and became ready in
329 seconds. It contained all seven required aliases and 2,549,358 documents
in total, including 2,505,900 job postings. The drill passed:

- healthy API and single-node leadership;
- exact alias targets and collection document counts;
- a representative job-posting search; and
- creation, retrieval, and deletion of a disposable probe collection.

The helper removed the container, restored data, generated API key, and
temporary repository access on every exit path. The redacted machine-readable
result is retained under the root-only incident evidence directory.

The final retained checkpoint after the credential rollback exercise was
`48b1e827`, created at 2026-08-03 11:40:20 UTC. A second isolated restore of
that exact snapshot returned 1,080,805,394 bytes and passed in 315 seconds.
It matched all seven aliases and the final production counts exactly: 5,072
companies, 2,505,900 job postings, 37,526 locations, 562 occupations, 36
seniorities, 186 technologies, and 77 watchlists. Health, leadership,
representative search, and disposable write/read/delete checks all passed.
The recovery container, restored data, helper, copied Restic binary, and
temporary credentials were then removed.

After backup and restore verification, generated key ID 23 was deleted and
key ID 27 remained active. The superseded environment rollback copy and the
temporary plaintext incident copy were deleted; the active value remains only
in the root-owned host environment and protected GitHub environment secret.

## Failure-path exercise

A known-invalid value produced a controlled backup failure while the installed
credential remained unchanged and authorized. The backup attempt failed in
about 0.1 seconds, preserved the last successful timestamp, and published the
Typesense `jobseek_backup_last_attempt_success` metric as 0. The Mimir ruler
owns a healthy `DataBackupFailed` rule with a 15-minute
hold and the tracking issue is the operator handoff. A subsequent real backup
restored the metric to 1 and removed the Typesense-specific active alert.

The full credential-rotation rollback path was also exercised. A throwaway
generated key passed the candidate probe and completed the required full
backup. The intentionally inactive timer then failed the later deployment
gate with exit code 3. The installer restored the original environment,
the original credential remained authorized, and the throwaway server key was
deleted. The timer was returned to enabled and active before the final restore.

## Permanent controls

- Backup artifacts now include the alias targets and per-alias document counts
  captured immediately before the consistent snapshot.
- Deployment probes a candidate key before mutation.
- A changed credential must produce a full successful backup before rotation
  is committed; later failure restores the previous host file.
- Disabled/inactive timers, failed services, and failed or stale status are
  deployment failures instead of advisory output.
- The repo-owned restore helper refuses a host with a production
  `typesense` container, binds only to loopback, validates contents, and
  cleans itself up.

Production evidence is stored root-only at
`/var/lib/jobseek-incidents/6120/20260803T103146Z`. It contains redacted
status, snapshot metadata, expected inventory, and restore result; it contains
no API key or repository password.
