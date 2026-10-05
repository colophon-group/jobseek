package queue

import _ "embed"

// Frozen legacy durable-identity statements execute in the existing owned
// transaction. They validate company ownership, preserve canonical posting IDs
// and archive prior outbound URLs before replacing the current URL.
//
//go:embed rich_monitor_identity_validate.sql
var richMonitorIdentityValidateSQL string

//go:embed rich_monitor_identity_diff.sql
var richMonitorIdentityDiffSQL string

//go:embed rich_monitor_identity_insert.sql
var richMonitorIdentityInsertSQL string

//go:embed rich_monitor_identity_enrich_insert.sql
var richMonitorIdentityEnrichInsertSQL string
