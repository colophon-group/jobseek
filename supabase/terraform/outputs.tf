output "production_project_ref" {
  description = "Supabase ref for the imported production project."
  value       = var.production_project_ref
}

output "production_branch_inventory" {
  description = "Current branches discovered from the live production project."
  value       = data.supabase_branch.production.branches
}

output "production_pooler_urls" {
  description = "Current Supabase pooler URLs for the production project."
  value       = data.supabase_pooler.production.url
  sensitive   = true
}

output "production_api_key_inventory" {
  description = "Current API key topology for production."
  value = {
    has_anon_key         = data.supabase_apikeys.production.anon_key != null
    has_publishable_key  = data.supabase_apikeys.production.publishable_key != null
    has_service_role_key = data.supabase_apikeys.production.service_role_key != null
    secret_key_names     = [for key in data.supabase_apikeys.production.secret_keys : key.name]
  }
  sensitive = true
}

output "staging_branch_id" {
  description = "Identifier of the managed staging branch."
  value       = supabase_branch.staging.id
}

output "staging_project_ref" {
  description = "Supabase project ref for the managed staging branch database."
  value       = supabase_branch.staging.database.id
}

output "staging_database_host" {
  description = "Host name for the staging branch database."
  value       = supabase_branch.staging.database.host
}

output "staging_database_port" {
  description = "Port for the staging branch database."
  value       = supabase_branch.staging.database.port
}

output "staging_database_user" {
  description = "Database user for the staging branch."
  value       = supabase_branch.staging.database.user
}

output "staging_database_password" {
  description = "Database password for the staging branch."
  value       = supabase_branch.staging.database.password
  sensitive   = true
}

output "staging_database_jwt_secret" {
  description = "JWT secret generated for the staging branch."
  value       = supabase_branch.staging.database.jwt_secret
  sensitive   = true
}
