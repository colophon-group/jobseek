# Protected backup restore fixtures

These exact reviewed migration bytes are test data for the independent backup
release. The production migration runner never loads this directory. Tests bind
their SHA-256 identities to the backup phase constants and require the actual
`apps/web/drizzle` files to match once those files exist.
