# Host reconciliation launcher

The systemd timer runs `go-typesense-exporter --reconcile` directly from the
immutable crawler image in `CRAWLER_IMAGE_REF`. The default slice repairs at
most 16 partitions; `--full` completes the remaining durable cycle. Both use
the existing host mutation lock, scoped credentials, 50-minute runtime budget,
and read-only container filesystem.

## Deployment order for the Go transition

1. Deploy the Go maintenance image from PR #10089 (v0.13.870) successfully.
   Its binary must support `--reconcile`; older images cannot run this wrapper.
2. Merge this launcher change. The `Deploy Crawler Reconciliation (Hetzner)`
   workflow installs it, records the exact revision and wrapper SHA-256, checks
   the installed contract, and starts a bounded reconciliation slice.
3. Inspect the service result and durable reconciliation output. A successful
   workflow smoke start proves launch only, not a completed repair slice.

Subsequent crawler deployments attest the new wrapper digest. Do not install
this wrapper before the supporting image or bypass the digest check. To roll
back to an image without Go reconciliation, first restore the prior launcher
through its deployment workflow and attest that contract, then use the
supported crawler rollback. Keep the existing selector/c1 rollback procedure
for crawler image changes.

The root host installer/state verifier still uses Python. This change removes
Python from the scheduled reconciliation process, not from every host utility.
