variable "supabase_access_token" {
  description = "Supabase personal access token used by the Terraform provider."
  type        = string
  sensitive   = true
}

variable "production_project_ref" {
  description = "Existing Supabase project ref for the production Jobseek database."
  type        = string
  default     = "rbjzdlsdovasviziflbp"
}

variable "organization_id" {
  description = "Supabase organization ID that owns the production project."
  type        = string
  default     = "snxalgdyhaqyyicgujna"
}

variable "production_project_name" {
  description = "Display name of the existing production Supabase project."
  type        = string
  default     = "job-seek"
}

variable "production_database_password" {
  description = "Current database password for the production project. Terraform keeps it ignored after import."
  type        = string
  sensitive   = true
}

variable "production_region" {
  description = "Supabase region for the existing production project."
  type        = string
  default     = "eu-central-1"
}

variable "staging_branch_name" {
  description = "Name of the persistent staging branch database."
  type        = string
  default     = "staging"
}

variable "staging_region" {
  description = "Region for the staging branch database. Defaults to production."
  type        = string
  default     = null
}

variable "production_api_settings_json" {
  description = "Optional serialized JSON for production API settings."
  type        = string
  default     = null
}

variable "production_auth_settings_json" {
  description = "Optional serialized JSON for production Auth settings."
  type        = string
  default     = null
}

variable "production_database_settings_json" {
  description = "Optional serialized JSON for production database settings."
  type        = string
  default     = null
}

variable "production_network_settings_json" {
  description = "Optional serialized JSON for production network settings."
  type        = string
  default     = null
}

variable "production_pooler_settings_json" {
  description = "Optional serialized JSON for production pooler settings."
  type        = string
  default     = null
}

variable "production_storage_settings_json" {
  description = "Optional serialized JSON for production storage settings."
  type        = string
  default     = null
}
