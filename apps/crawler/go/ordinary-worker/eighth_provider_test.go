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

const eighthCVSection = "12345678-1234-1234"

func eighthFixtureSource(provider string) string {
	switch provider {
	case "earcu", "earcu/proxy":
		return "https://jobs.example.com/jobs/vacancy/find/results/"
	case "cvwarehouse":
		return "https://tenant.cvw.io/?lang=nl-BE"
	case "woowa/youths":
		return "https://career.woowayouths.com/"
	case "woowa/bmart":
		return "https://bmart-career.woowayouths.com/"
	default:
		return "https://career.woowahan.com/"
	}
}

func eighthFixtureHandler(provider, mode string, requests *atomic.Int32) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		count := requests.Add(1)
		if r.Method != "GET" {
			w.WriteHeader(405)
			return
		}
		if mode == "retry" && count == 1 {
			w.WriteHeader(429)
			return
		}
		if mode == "reserved" {
			w.Header().Set("TDM-Reservation", "1")
		}
		switch strings.Split(provider, "/")[0] {
		case "earcu":
			if r.URL.Path != "/jobs/allvacancies/" {
				w.WriteHeader(404)
				return
			}
			if mode == "partial" {
				fmt.Fprint(w, `<positions><position><JobTitle>Engineer</JobTitle></position></positions>`)
				return
			}
			fmt.Fprint(w, `<positions><position><DescriptionURL>/jobs/vacancy/12</DescriptionURL><JobTitle>Engineer</JobTitle><Description><![CDATA[<p>Build</p>]]></Description><Locations><Location>Zürich</Location></Locations><LastPublishedDate>2026-10-08</LastPublishedDate></position></positions>`)
		case "cvwarehouse":
			if r.URL.Query().Get("section") == "" {
				fmt.Fprintf(w, `<p>CVWarehouse</p><a href="?section=%s"><span class="badge">2</span></a>`, eighthCVSection)
				return
			}
			id := "12"
			if r.URL.Path == "/english" {
				id = "13"
				if mode == "partial" {
					w.WriteHeader(502)
					return
				}
			}
			if mode == "late-reserved" && id == "13" {
				w.Header().Set("TDM-Reservation", "1")
			}
			fmt.Fprintf(w, `<div id="language-modal"><a href="/english?section=%s&amp;lang=en">English</a></div><div data-item-collection><a data-jobid="%s" href="/?job=%s&amp;lang=nl-BE">Job</a></div><section data-jobdetail-job-id="%s"><h2 class="job-title">Engineer</h2><div class="additional-data"><span class="location">Brussels</span></div><div class="jobDescriptionText"><p>Build</p></div></section>`, eighthCVSection, id, id, id)
		case "woowa":
			if strings.HasSuffix(r.URL.Path, "recruits") {
				page := r.URL.Query().Get("page")
				if r.URL.Query().Get("size") != "500" || r.URL.Query().Get("sort") != "updateDate,desc" {
					w.WriteHeader(400)
					return
				}
				total, id := 2, "R-1"
				if page == "1" {
					id = "R-2"
					if mode == "partial" {
						total = 3
					}
					if mode == "late-reserved" {
						w.Header().Set("TDM-Reservation", "1")
					}
				}
				fmt.Fprintf(w, `{"code":"2000","data":{"totalSize":%d,"list":[{"recruitNumber":%q,"recruitName":"Engineer","description":"Seoul"}]}}`, total, id)
				return
			}
			id := r.URL.Path[strings.LastIndex(r.URL.Path, "/")+1:]
			if mode == "detail-reserved" && id == "R-2" {
				w.Header().Set("TDM-Reservation", "1")
			}
			if mode == "identity" && id == "R-2" {
				id = "wrong"
			}
			fmt.Fprintf(w, `{"code":"2000","data":{"recruitNumber":%q,"recruitName":"Engineer","recruitContents":"<p>Build</p>","employmentType":{"recruitItemCode":"BA002001"},"recruitOpenDate":"2026-10-08","applicantCheckList":[{"recruitItemName":"Build","recruitItemRemark":"Platform"}]}}`, id)
		}
	}
}

func TestEighthProvidersHTTPWholeInventoryAndPolicy(t *testing.T) {
	for _, provider := range []string{"earcu", "cvwarehouse", "woowa/brothers", "woowa/youths", "woowa/bmart"} {
		for _, mode := range []string{"success", "reserved", "partial", "retry", "late-reserved", "identity", "detail-reserved"} {
			if provider == "earcu" && (mode == "late-reserved" || mode == "identity" || mode == "detail-reserved") || provider == "cvwarehouse" && (mode == "identity" || mode == "detail-reserved") {
				continue
			}
			t.Run(provider+"/"+mode, func(t *testing.T) {
				config := map[string]string{"crawler_type": strings.Split(provider, "/")[0], "board_url": eighthFixtureSource(provider), "metadata": `{"scraper_type":"skip"}`, "monitor_needs_browser": "0"}
				o, err := api.EighthProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
				if err != nil {
					t.Fatal(err)
				}
				p := queue.GreenhouseMonitorProfile{Provider: o.Provider, Profile: o.Profile(), Endpoint: o.ListingURL()}
				var requests, waits atomic.Int32
				client := verifiedClaimFixtureClient(t, eighthFixtureHandler(provider, mode, &requests))
				got, err := FetchEighthProvidersHTTP(context.Background(), client.client, p, config, func(ctx context.Context, d time.Duration) error { waits.Add(1); return ctx.Err() })
				fail := mode != "success" && mode != "retry"
				if fail {
					if err == nil || len(got.Jobs) != 0 {
						t.Fatal("incomplete inventory accepted", err, len(got.Jobs))
					}
					if strings.Contains(mode, "reserved") {
						var reservation *policy.Reservation
						if !errors.As(err, &reservation) {
							t.Fatal("publisher decision lost", err)
						}
					}
					return
				}
				want := 2
				if provider == "earcu" {
					want = 1
				}
				if err != nil || len(got.Jobs) != want || got.Truncated {
					t.Fatal("complete inventory failed", err, len(got.Jobs))
				}
				if mode == "retry" && waits.Load() != 1 {
					t.Fatal("bounded retry lost", waits.Load())
				}
				for _, job := range got.Jobs {
					if job.Title == nil || *job.Title != "Engineer" || job.Description == nil || !strings.Contains(*job.Description, "Build") {
						t.Fatal("canonical content lost")
					}
					if strings.HasPrefix(provider, "woowa/") && !strings.HasPrefix(job.SourceIdentity, "woowa:"+o.Variant+":R-") {
						t.Fatal("provider source identity lost", job.SourceIdentity)
					}
				}
			})
		}
	}
}

func TestEighthProviderGoneOnlyInitialWoowaListing(t *testing.T) {
	config := map[string]string{"crawler_type": "woowa", "board_url": eighthFixtureSource("woowa"), "metadata": "{}", "monitor_needs_browser": "0"}
	o, _ := api.EighthProviderOptionsFromMetadata("woowa", config["board_url"], "{}")
	p := queue.GreenhouseMonitorProfile{Provider: "woowa", Profile: o.Profile(), Endpoint: o.ListingURL()}
	for _, detail := range []bool{false, true} {
		t.Run(fmt.Sprint(detail), func(t *testing.T) {
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if detail && r.URL.RawQuery != "" {
					json.NewEncoder(w).Encode(map[string]any{"code": "2000", "data": map[string]any{"totalSize": 1, "list": []any{map[string]any{"recruitNumber": "R-1"}}}})
					return
				}
				w.WriteHeader(404)
			}))
			got, err := FetchEighthProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
			var failure *DiscoveryError
			if err == nil || len(got.Jobs) != 0 || !errors.As(err, &failure) || (failure.Kind == "provider_gone") == detail {
				t.Fatal("gone scope differs", err)
			}
		})
	}
}

func TestEighthCVWarehouseRootJobsSurviveGenericFiltering(t *testing.T) {
	source := "https://tenant.cvw.io/?job=12"
	inventory, err := NormalizeRichInventory(context.Background(), "https://tenant.cvw.io/", []RichMonitorJob{{URL: source}}, false)
	if err != nil || len(inventory.Jobs) != 1 || inventory.Jobs[0].URL != source {
		t.Fatal("provider job lost before persistence", err)
	}
	if classifyJobURL("https://tenant.cvw.io/", "") != "bare_host" || classifyJobURL("https://other.example/?job=12", "") != "bare_host" {
		t.Fatal("generic root filtering weakened")
	}
}

func TestEighthEArcuAutodetectionAndRedirectFailure(t *testing.T) {
	for _, mode := range []string{"fallback", "foreign-redirect", "malformed-first"} {
		t.Run(mode, func(t *testing.T) {
			config := map[string]string{"crawler_type": "earcu", "board_url": eighthFixtureSource("earcu"), "metadata": "{}", "monitor_needs_browser": "0"}
			o, _ := api.EighthProviderOptionsFromMetadata("earcu", config["board_url"], "{}")
			p := queue.GreenhouseMonitorProfile{Provider: "earcu", Profile: o.Profile(), Endpoint: o.ListingURL()}
			var foreign atomic.Int32
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Host != "jobs.example.com" {
					foreign.Add(1)
				}
				if r.URL.Path == "/jobs/allvacancies/" {
					switch mode {
					case "fallback":
						w.WriteHeader(404)
					case "foreign-redirect":
						http.Redirect(w, r, "https://other.example/allvacancies/", 302)
					default:
						fmt.Fprint(w, "<html/> ")
					}
					return
				}
				fmt.Fprint(w, `<positions><position><DescriptionURL>/vacancy/12</DescriptionURL><JobTitle>Engineer</JobTitle></position></positions>`)
			}))
			got, err := FetchEighthProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
			if mode == "fallback" {
				if err != nil || len(got.Jobs) != 1 || got.Jobs[0].URL != "https://jobs.example.com/vacancy/12" {
					t.Fatal("candidate fallback failed", err)
				}
			} else if err == nil || len(got.Jobs) != 0 {
				t.Fatal("invalid candidate became inventory")
			}
			if foreign.Load() != 0 {
				t.Fatal("redirect left provider before validation")
			}
		})
	}
}
