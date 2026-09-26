package personio

import (
	"strings"
	"testing"
)

func TestParseReaderMatchesRichPositionShape(t *testing.T) {
	feed := `<workzag-jobs><position><id>12345</id><name>Engineer</name>` +
		`<office>Zurich</office><employmentType>permanent</employmentType>` +
		`<schedule>full-time</schedule><createdAt>2026-09-25</createdAt>` +
		`<department>Technology</department><keywords>Go</keywords>` +
		`<jobDescriptions><jobDescription><name>Role</name><value>&lt;p&gt;Build&lt;/p&gt;</value></jobDescription>` +
		`<jobDescription><name>Requirements</name><value>Go experience</value></jobDescription>` +
		`</jobDescriptions></position><position><name>Missing ID</name></position></workzag-jobs>`
	jobs := []Job{}
	positions, count, err := ParseReader(strings.NewReader(feed), "acme", "de", func(job Job) error {
		jobs = append(jobs, job)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if positions != 2 || count != 1 || len(jobs) != 1 {
		t.Fatalf("positions=%d count=%d jobs=%+v", positions, count, jobs)
	}
	job := jobs[0]
	if job.URL != "https://acme.jobs.personio.de/job/12345" || job.Title == nil || *job.Title != "Engineer" || job.Description == nil || *job.Description != "<h3>Role</h3>\n<p>Build</p>\n<h3>Requirements</h3>\nGo experience" || len(job.Locations) != 1 || job.Locations[0] != "Zurich" || job.EmploymentType == nil || *job.EmploymentType != "full-time" || job.Metadata["department"] != "Technology" || job.Metadata["keywords"] != "Go" {
		t.Fatalf("unexpected rich job: %+v", job)
	}
}

func TestParseReaderFirstFieldAndSpecificEmployment(t *testing.T) {
	feed := `<root><position><id>42</id><id>43</id><employmentType>INTERN</employmentType>` +
		`<schedule>part-time</schedule><office>Berlin</office><office>Paris</office></position></root>`
	jobs := []Job{}
	_, count, err := ParseReader(strings.NewReader(feed), "example", "com", func(job Job) error {
		jobs = append(jobs, job)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if count != 1 || jobs[0].URL != "https://example.jobs.personio.com/job/42" || jobs[0].Locations[0] != "Berlin" || jobs[0].EmploymentType == nil || *jobs[0].EmploymentType != "intern" {
		t.Fatalf("unexpected first field or employment value: %+v", jobs)
	}
}
