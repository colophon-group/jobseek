locals {
  staging_region = coalesce(var.staging_region, var.production_region)
}

data "supabase_branch" "production" {
  parent_project_ref = var.production_project_ref
}

data "supabase_apikeys" "production" {
  project_ref = var.production_project_ref
}

data "supabase_pooler" "production" {
  project_ref = var.production_project_ref
}

resource "supabase_project" "production" {
  organization_id         = var.organization_id
  name                    = var.production_project_name
  database_password       = var.production_database_password
  region                  = var.production_region
  legacy_api_keys_enabled = true

  lifecycle {
    ignore_changes = [database_password]
  }
}

resource "supabase_settings" "production" {
  project_ref = var.production_project_ref
  api         = var.production_api_settings_json
  auth        = var.production_auth_settings_json
  database    = var.production_database_settings_json
  network     = var.production_network_settings_json
  pooler      = var.production_pooler_settings_json
  storage     = var.production_storage_settings_json
}

resource "supabase_branch" "staging" {
  git_branch         = var.staging_branch_name
  parent_project_ref = var.production_project_ref
  region             = local.staging_region
}

resource "supabase_settings" "staging" {
  project_ref = supabase_branch.staging.database.id
  api         = var.production_api_settings_json
  auth        = var.production_auth_settings_json
  database    = var.production_database_settings_json
  network     = var.production_network_settings_json
  pooler      = var.production_pooler_settings_json
  storage     = var.production_storage_settings_json
}
