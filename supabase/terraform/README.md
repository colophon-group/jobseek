# Supabase IaC

This module tracks the existing Jobseek Supabase project as `production` and provisions a persistent `staging` database as a Supabase branch.

Current setup discovered with Supabase CLI:
- Organization ID: `snxalgdyhaqyyicgujna`
- Production project ref: `rbjzdlsdovasviziflbp`
- Production project name: `job-seek`
- Region: `eu-central-1` (`Central EU / Frankfurt`)
- Existing branches: only the default `main` branch
- Existing endpoints: one pooled Postgres URL and one direct Postgres URL
- Existing key topology: legacy `anon` + `service_role`, plus one publishable key and one secret key

Why this shape:
- Supabase officially supports importing an existing project into Terraform.
- Supabase branches are separate databases derived from the parent project, which gives staging the same schema as production at creation time.

Official references:
- Supabase Terraform provider: https://supabase.com/docs/guides/deployment/terraform
- Importing an existing project: https://supabase.com/docs/guides/deployment/terraform/tutorial
- Provider reference: https://supabase.com/docs/guides/deployment/terraform/reference

## Files

- `versions.tf`: Terraform and provider versions
- `main.tf`: imports the existing production project, snapshots the current live topology through Terraform data sources, and creates the staging branch
- `variables.tf`: required inputs
- `outputs.tf`: production topology plus staging connection details surfaced by Terraform
- `terraform.tfvars.example`: starter values for this repo

## Usage

1. Copy the example vars file:

```bash
cp supabase/terraform/terraform.tfvars.example supabase/terraform/terraform.tfvars
```

2. Fill in the real values:
- `supabase_access_token`
- `production_database_password`

3. Apply the module:

```bash
terraform -chdir=supabase/terraform init
terraform -chdir=supabase/terraform import \
  'supabase_project.production' \
  rbjzdlsdovasviziflbp
terraform -chdir=supabase/terraform import \
  'supabase_settings.production' \
  rbjzdlsdovasviziflbp
terraform -chdir=supabase/terraform apply
```

The first apply will:
- use the explicit `terraform import` step above to attach the existing production
  project `rbjzdlsdovasviziflbp` to Terraform state
- attach the production settings object to Terraform state
- create a persistent `staging` branch database from production

If you want the exact current production settings JSON committed to code instead of
left as variables:

```bash
terraform -chdir=supabase/terraform state show supabase_settings.production
```

Copy the serialized JSON values from that output into `terraform.tfvars`, then run
`terraform apply` again. The CLI does not expose those settings directly, so the
imported Terraform state is the reliable source of truth for them.

## Schema parity

The branch is cloned from production, so staging starts with the same schema as production.

For ongoing schema changes, continue applying the app migrations to every environment:

```bash
cd apps/web
DATABASE_URL_UNPOOLED="<target-db-url>" pnpm db:migrate
```

Run that once against production and once against the staging branch connection string exposed by Terraform outputs.

## What Is And Is Not Codified

Codified:
- production project identity and region
- production legacy API key mode
- production live branch inventory via Terraform data source
- production pooled connection URLs via Terraform data source
- staging branch creation
- production and staging settings resources

Observed but not safely recreated as standalone Terraform resources:
- individual built-in API keys

The provider exposes key inventory as a data source, which this module outputs, but
the built-in key set is not modeled here as independent createable resources because
the provider schema does not expose enough stable inputs to recreate the existing
legacy and default keys safely.
