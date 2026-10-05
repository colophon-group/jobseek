package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestJobylonMatchesActualPythonRichFieldsAndRequests(t *testing.T) {
	data, err := os.ReadFile("testdata/python_jobylon.json")
	var corpus struct {
		Cases []struct {
			Name, Body string
			Requests   []struct{ Method, URL string }
			Expected   struct {
				Error, Truncated bool
				Jobs             []map[string]any
			}
		}
	}
	if err != nil || json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) != 19 {
		t.Fatal("actual Python Jobylon corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			requests := []struct{ Method, URL string }{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests = append(requests, struct{ Method, URL string }{r.Method, "https://" + r.Host + r.URL.String()})
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				fmt.Fprint(w, c.Body)
			}))
			p := queue.GreenhouseMonitorProfile{Provider: "jobylon", Profile: "jobylon.embed-items/v1", Token: "companies/123", Endpoint: c.Requests[0].URL}
			if c.Name == "group-precedence" {
				p.Token = "company-groups/456"
			}
			result, err := discoverJobylonInventory(context.Background(), client.client, p)
			if (err != nil) != c.Expected.Error || !reflect.DeepEqual(requests, c.Requests) {
				t.Fatal("inventory failure or requests differ", err)
			}
			if c.Expected.Error {
				return
			}
			actual := []map[string]any{}
			for _, j := range result.Jobs {
				fields := map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "language": j.Language, "metadata": j.Metadata}
				// Detach Go pointers/slices through the same nullable JSON boundary.
				body, _ := json.Marshal(fields)
				var projected map[string]any
				_ = json.Unmarshal(body, &projected)
				actual = append(actual, projected)
			}
			for _, job := range c.Expected.Jobs {
				delete(job, "extras")
				delete(job, "localizations")
				delete(job, "base_salary")
				delete(job, "source_identity")
			}
			if !reflect.DeepEqual(actual, c.Expected.Jobs) || result.Truncated != c.Expected.Truncated {
				t.Fatalf("rich provider fields differ: %#v / %#v", actual, c.Expected.Jobs)
			}
		})
	}
}

func TestJobylonUniqueCapKeepsFirstContentAndSuppressesDisappearance(t *testing.T) {
	var b strings.Builder
	b.WriteString("JBL.embed_v2['jobs'] = [")
	for i := 1; i <= 50_001; i++ {
		if i > 1 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, `{id:%d,url:'/jobs/%d/',title:'Engineer'}`, i, i)
	}
	b.WriteString("]")
	jobs, truncated, err := parseJobylonInventory(b.String())
	if err != nil || !truncated || len(jobs) != 50_000 || jobs[49_999].URL != "https://emp.jobylon.com/jobs/50000/" {
		t.Fatal("unique provider cap differs", err, len(jobs), truncated)
	}
}
