package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func tenthFixtureSource(provider string) string {
	switch provider {
	case "intervieweb":
		return "https://fixture.intervieweb.it/en/career"
	case "typify":
		return "https://example.com/jobs"
	case "universia":
		return "https://jobboard.universia.net/sample"
	default:
		return "https://apply.jobappnetwork.com/sample"
	}
}

func tenthFixtureHandler(provider, mode string, calls *atomic.Int32) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		if mode == "reserved" {
			w.Header().Set("TDM-Reservation", "1")
		}
		if mode == "redirect" {
			http.Redirect(w, r, "https://foreign.example/private", 302)
			return
		}
		if mode == "retry" && n == 1 || mode == "exhausted" {
			w.WriteHeader(403)
			return
		}
		if mode == "gone" {
			w.WriteHeader(404)
			return
		}
		switch provider {
		case "intervieweb":
			if r.Method == "GET" {
				fmt.Fprint(w, `<input id="url-for-announces" value="/app.php?module=newcareer"><a data-order="name" class="active">Name</a><script>{"section":"jobs"}</script>vacancyListCareer researchAnnounces Page 1 of 2 <a href="/jobs/one/en/">One</a>`)
				return
			}
			if mode == "partial" {
				fmt.Fprint(w, `{"success":true,"data":""}`)
				return
			}
			fmt.Fprint(w, `{"success":true,"data":"Page 2 of 2 <a href='/jobs/two/en/'>Two</a>"}`)
		case "typify":
			if r.Method == "GET" {
				fmt.Fprint(w, `window.typify={language:'nl'};<input class='cb-function' data-id='1'>`)
				return
			}
			if err := r.ParseForm(); err != nil {
				tenthWriteError(w)
				return
			}
			if r.Form.Get("map") == "0" {
				fmt.Fprint(w, `{"pagination":{"total":1}}`)
				return
			}
			if mode == "partial" {
				fmt.Fprint(w, `{"pagination":{"total":2,"total_pages":1},"results":[{"title":"Engineer","url":"/job/one"}]}`)
				return
			}
			fmt.Fprint(w, `{"pagination":{"total":1,"total_pages":1},"results":[{"title":"Engineer","url":"/job/one","location":{"label":"Rotterdam"}}]}`)
		case "universia":
			board := "11111111-1111-4111-8111-111111111111"
			id := "22222222-2222-4222-8222-222222222222"
			if strings.Contains(r.URL.Path, "/config/") {
				json.NewEncoder(w).Encode(map[string]any{"slug": "sample", "entity": map[string]any{"id": board, "entityType": "company"}, "languages": []string{"es-CO"}})
				return
			}
			total := 1
			if mode == "partial" {
				total = 2
			}
			json.NewEncoder(w).Encode(map[string]any{"offset": 0, "limit": 100, "size": 1, "total": total, "totalPages": 1, "results": []any{map[string]any{"identifier": id, "boards": []string{board}, "status": "published", "title": "Engineer", "description": "<p>Build Go.</p>", "url": "https://www.universia.net/es/empleo/" + id + "/engineer.html?referer=" + board, "employmentType": "FULL_TIME", "jobLocationType": "TELECOMMUTE", "jobLocation": map[string]any{"address": map[string]any{"streetAddress": "Bogotá"}}}}})
		case "talentreef":
			if r.Method == "GET" {
				fmt.Fprint(w, `[{"published":true,"locale":"en","clientId":"123","brands":["One"]}]`)
				return
			}
			total := 1
			hits := []any{map[string]any{"_source": map[string]any{"jobId": "17", "positionType": "Engineer", "description": "<p>Build Go.</p>", "address": map[string]any{"city": "Zürich"}, "category": "Full Time"}}}
			if mode == "partial" {
				total = 2
				if n > 2 {
					hits = []any{}
				}
			}
			json.NewEncoder(w).Encode(map[string]any{"hits": map[string]any{"total": total, "hits": hits}})
		}
	}
}

func tenthWriteError(w http.ResponseWriter) { w.WriteHeader(400) }

func TestTenthProvidersBoundedHTTPCompleteFieldsFailuresAndPublisherReservation(t *testing.T) {
	for _, provider := range []string{"intervieweb", "typify", "universia", "talentreef"} {
		for _, mode := range []string{"complete", "partial", "reserved", "redirect", "retry", "exhausted", "gone"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				source := tenthFixtureSource(provider)
				o, err := api.TenthProviderOptionsFromMetadata(provider, source, "{}")
				if err != nil {
					t.Fatal(err)
				}
				var calls atomic.Int32
				waits := 0
				client := verifiedClaimFixtureClient(t, tenthFixtureHandler(provider, mode, &calls))
				p := queue.GreenhouseMonitorProfile{Provider: provider, Profile: o.Profile(), Endpoint: o.ListingURL()}
				out, err := FetchTenthProvidersHTTP(context.Background(), client.client, p, map[string]string{"board_url": source, "metadata": "{}", "monitor_needs_browser": "0"}, func(context.Context, time.Duration) error { waits++; return nil })
				if mode != "complete" && mode != "retry" {
					if err == nil || len(out.Jobs) != 0 {
						t.Fatal("failed whole inventory reached output", err, len(out.Jobs))
					}
					if mode == "reserved" {
						var reservation *policy.Reservation
						if !errors.As(err, &reservation) {
							t.Fatal("publisher reservation was swallowed", err)
						}
					}
					if mode == "exhausted" && (calls.Load() != 3 || waits != 2) {
						t.Fatal("retry was unbounded")
					}
					return
				}
				want := 1
				if provider == "intervieweb" {
					want = 2
				}
				if err != nil || out.Truncated || len(out.Jobs) != want {
					t.Fatal("whole inventory differs", len(out.Jobs), err)
				}
				if mode == "retry" && waits != 1 {
					t.Fatal("transient retry did not wait")
				}
				job := out.Jobs[0]
				if provider == "intervieweb" {
					if !job.URLOnly {
						t.Fatal("URL inventory became rich")
					}
					return
				}
				if job.Title == nil || *job.Title != "Engineer" || len(job.Locations) != 1 {
					t.Fatal("title or location fields omitted", job)
				}
				if provider == "universia" && (job.Description == nil || *job.Description != "<p>Build Go.</p>" || job.EmploymentType != "full_time" || job.JobLocationType != "remote" || job.SourceIdentity != "universia:11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222") {
					t.Fatal("canonical fields or identity changed", job)
				}
				if provider == "talentreef" && job.SourceIdentity != "talentreef:123:17" {
					t.Fatal("client identity omitted")
				}
			})
		}
	}
}
