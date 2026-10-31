# Doppler migration of existing credentials

The owner can copy existing GitHub Actions secrets directly into an isolated Doppler import configuration using **Migrate existing secrets to Doppler**. This implements Doppler's [documented import approach](https://docs.doppler.com/docs/github-actions#importing-secrets-from-github-actions), with fixed destinations, an explicit secret-name allowlist and a short-lived bootstrap credential.

The workflow only accepts an owner dispatch from exact current `main`. It preserves existing environment protections. The checkout is bound to the reviewed revision, with Git credentials disabled. It does not deploy anything, modify existing secrets, log values, upload artifacts, or accept arbitrary destination URLs, project names or configuration names.

1. Review `scripts/doppler-migration-allowlist.json` against the existing repository/environment secret names. Update the reviewed manifest if a legitimate source has changed.
2. Create an empty configuration in project `justup-import`, environment `import`: `import_repository`, `import_production`, `import_migrations`, or `import_migration_drift` as applicable.
3. Create a short-lived read/write service token for **that one import configuration**. This token must not access the runtime, deployment, administration or dormant stores.
4. Put it in the repository secret `DOPPLER_MIGRATION_IMPORT_TOKEN` using stdin or the provider UI. Never pass it as a workflow input or command argument.
5. Dispatch from the exact current main SHA, selecting the existing source scope. The program uploads only allowlisted source keys directly over verified HTTPS, then compares a fresh destination read in memory. Missing values, a mismatched token scope, conflicting destination values, redirects and unsuccessful verification all stop the run. The log reports only counts and non-secret failure information.
6. Revoke the temporary token and delete `DOPPLER_MIGRATION_IMPORT_TOKEN` immediately after the run, including on failure. Repeat with a separate scoped token for each source.
7. Move verified values from the import inbox into the appropriate per-service runtime, deployment, administration or dormant configuration. Preserve provenance and distinct values. Verify the classified destinations, then remove the temporary inbox values/configurations. Do not leave reusable broad import tokens behind.

This copies secrets into the manager. Switching live consumers to Doppler is a separate deployment change and must follow the service's existing deployment, health and rollback contract. Do not delete a source credential or protected host file while it is still in use. Possibly exposed credentials require provider-side revocation/rotation; moving them is not remediation.

GitHub cannot disclose these values through its API. This workflow is intentionally manual and contains no pull-request or scheduled trigger. Vercel sensitive variables that are absent from other original sources must be recovered or rotated through their provider workflow; do not add an application endpoint to extract them.
