package queue

// These fields already have original-compatible selected writes in the shared
// detail processor. Metadata participates in the original availability check;
// it does not create a canonical column write. Language and extras stay excluded.
var detailEnrichmentFields = map[string]bool{
	"title": true, "description": true, "locations": true,
	"employment_type": true, "job_location_type": true,
	"date_posted": true, "base_salary": true,
	"metadata": true,
}
