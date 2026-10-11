package queue

// These fields already have original-compatible selected writes in the shared
// detail processor. Language, extras and metadata need separate writer proof.
var detailEnrichmentFields = map[string]bool{
	"title": true, "description": true, "locations": true,
	"employment_type": true, "job_location_type": true,
	"date_posted": true, "base_salary": true,
}
