package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"sort"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type umantisReferenceCase struct {
	Name, Listing string
	Metadata      map[string]any
	Responses     map[string]struct {
		Status int
		Body   string
	}
	URLs, Calls []string
	Error       bool
}

func umantisReferenceCases(t *testing.T) []umantisReferenceCase {
	t.Helper()
	var corpus struct{ Discovery []umantisReferenceCase }
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_umantis.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Discovery) != 10 {
		t.Fatal("actual Python Umantis corpus missing", err)
	}
	return corpus.Discovery
}
func umantisReferenceHandler(t *testing.T, c umantisReferenceCase, reserved bool, transient bool) http.HandlerFunc {
	t.Helper()
	seen := map[string]int{}
	return func(w http.ResponseWriter, r *http.Request) {
		resource := "https://recruitingapp-3040.umantis.com" + r.URL.RequestURI()
		response, found := c.Responses[resource]
		if !found {
			t.Error("unexpected resource", resource)
			w.WriteHeader(400)
			return
		}
		seen[resource]++
		if r.URL.Path == "/Jobs/3" && !strings.Contains(r.URL.RawQuery, "tc1184173=") {
			if _, err := r.Cookie("fixture-tenant"); err == nil {
				t.Error("fresh root reused a prior cookie")
			}
			http.SetCookie(w, &http.Cookie{Name: "fixture-tenant", Value: "32", Path: "/"})
		} else if cookie, err := r.Cookie("fixture-tenant"); err != nil || cookie.Value != "32" {
			t.Error("tenant cookie lost", err)
		}
		if reserved && strings.HasPrefix(r.URL.Path, "/Vacancies/2/") {
			w.Header().Set("Content-Type", "text/html")
			fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
			return
		}
		if transient && strings.Contains(r.URL.RawQuery, "tc1184173=") && seen[resource] == 1 {
			w.WriteHeader(503)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(response.Status)
		fmt.Fprint(w, response.Body)
	}
}
func TestUmantisHTTPMatchesPythonInventoriesIsolatesCookiesAndRetries(t *testing.T) {
	for _, c := range umantisReferenceCases(t) {
		t.Run(c.Name, func(t *testing.T) {
			body, _ := json.Marshal(c.Metadata)
			config := map[string]string{"board_url": c.Listing, "metadata": string(body), "monitor_needs_browser": "0"}
			p := queue.GreenhouseMonitorProfile{Provider: "umantis", Profile: "umantis.listing-urls/v1", Endpoint: c.Listing}
			client := verifiedClaimFixtureClient(t, umantisReferenceHandler(t, c, false, true))
			waits := 0
			for iteration := 0; iteration < 2; iteration++ {
				out, err := FetchUmantisHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
				if (err != nil) != c.Error || out.Truncated || err != nil && len(out.Jobs) != 0 {
					t.Fatal(err, out, c.Error)
				}
				if !c.Error {
					urls := []string{}
					for _, job := range out.Jobs {
						if !job.URLOnly {
							t.Fatal("unconfigured rich rows", job)
						}
						urls = append(urls, job.URL)
					}
					sort.Strings(urls)
					a, _ := json.Marshal(urls)
					b, _ := json.Marshal(c.URLs)
					if string(a) != string(b) {
						t.Fatal(urls, c.URLs)
					}
				}
			}
			if c.Name == "strict-complete" && waits != 1 {
				t.Fatal("bounded transient retry omitted", waits)
			}
		})
	}
}
func TestUmantisHTTPPartialRichFieldsAndLatePublisherReservation(t *testing.T) {
	c := umantisReferenceCases(t)[0]
	c.Metadata["scraper_type"] = "dom"
	c.Metadata["scraper_config"] = map[string]any{"enrich": []string{"description", "locations"}}
	body, _ := json.Marshal(c.Metadata)
	config := map[string]string{"board_url": c.Listing, "metadata": string(body), "monitor_needs_browser": "0"}
	p := queue.GreenhouseMonitorProfile{Provider: "umantis", Profile: "umantis.listing-urls/v1", Endpoint: c.Listing}
	for _, reserved := range []bool{false, true} {
		client := verifiedClaimFixtureClient(t, umantisReferenceHandler(t, c, reserved, false))
		out, err := FetchUmantisHTTP(context.Background(), client.client, p, config, noSecondaryWait)
		if reserved {
			var reservation *policy.Reservation
			if !errors.As(err, &reservation) || len(out.Jobs) != 0 {
				t.Fatal("late reservation lost", err, out)
			}
			continue
		}
		if err != nil || len(out.Jobs) != 2 {
			t.Fatal(err, out)
		}
		for _, job := range out.Jobs {
			if job.URLOnly || job.Title == nil || *job.Title != "Engineer" || len(job.Locations) != 1 || job.Locations[0] != "Neuchâtel CH" || job.EmploymentType != "Full time" || job.Description != nil {
				t.Fatal("partial rich fields differ", job)
			}
		}
	}
}
func TestRealUmantisCompleteAndFailedInventoryCanonicalSettlement(t *testing.T) {
	cases := umantisReferenceCases(t)
	for _, index := range []int{0, 1, 2, 5, 8} {
		c := cases[index]
		t.Run(c.Name, func(t *testing.T) {
			c.Metadata["scraper_type"] = "dom"
			c.Metadata["scraper_config"] = map[string]any{"steps": []any{map[string]any{"tag": "h1", "field": "title"}}}
			body, _ := json.Marshal(c.Metadata)
			f := privateRichPipelineFixtureURL(t, "umantis", string(body), c.Listing)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			client := verifiedClaimFixtureClient(t, umantisReferenceHandler(t, c, false, false))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal(err, result)
			}
			assertRichDeadlineAndLease(t, f, "umantis")
			var active bool
			var title string
			var failures int
			if err := f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures); err != nil {
				t.Fatal(err)
			}
			if c.Error {
				if !active || title != "Original" || failures != 1 || result.Batches.Inserted != 0 {
					t.Fatal("failed prefix changed canonical inventory", active, title, failures, result.Batches)
				}
				return
			}
			if result.Batches.Inserted != len(c.URLs) || failures != 0 {
				t.Fatal(result.Batches, failures)
			}
			var count int
			if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND next_scrape_at IS NOT NULL AND cardinality(titles)=0", f.board, f.original).Scan(&count); err != nil || count != len(c.URLs) {
				t.Fatal("URL inventory lost detail intent", err, count)
			}
		})
	}
}
