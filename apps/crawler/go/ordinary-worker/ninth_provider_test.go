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

func ninthFixtureSource(provider string) string {
	switch provider {
	case "beehire":
		return "https://app.beehire.com/career/tenant"
	case "hirehive":
		return "https://tenant.hirehive.com/"
	case "welcometothejungle":
		return "https://www.welcometothejungle.com/fr/companies/tenant/jobs"
	case "computrabajo":
		return "https://tenant.pandape.infojobs.com.br/"
	case "computrabajo/proxy":
		return "https://tenant.pandape.computrabajo.com/"
	default:
		return "https://www.ycombinator.com/companies/tenant/jobs"
	}
}

func ninthFixtureMetadata(provider string) string {
	if provider == "computrabajo/proxy" {
		return `{"scraper_type":"json-ld","proxy":true}`
	}
	if provider == "ycombinator" {
		return `{"scraper_type":"json-ld"}`
	}
	if provider == "hirehive" {
		return `{"scraper_type":"skip","defaults":{"job_location_type":"hybrid"}}`
	}
	return `{"scraper_type":"skip"}`
}

func ninthFixtureHandler(provider, mode string, requests *atomic.Int32) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		request := requests.Add(1)
		if mode == "reserved" {
			w.Header().Set("TDM-Reservation", "1")
		}
		if mode == "redirect" {
			w.Header().Set("Location", "https://foreign.example/private")
			w.WriteHeader(302)
			return
		}
		if mode == "gone" {
			w.WriteHeader(404)
			return
		}
		if mode == "retry" && request == 1 {
			w.WriteHeader(429)
			return
		}
		switch strings.Split(provider, "/")[0] {
		case "ycombinator":
			if mode == "partial" {
				w.WriteHeader(502)
				return
			}
			fmt.Fprint(w, `<a href="/companies/tenant/jobs/Ab1-engineer">Job</a><a href="/companies/tenant/jobs/Ab2-engineer">Job</a>`)
		case "beehire":
			if mode == "partial" {
				fmt.Fprint(w, `{"campaigns":[{"title":null}]}`)
				return
			}
			fmt.Fprint(w, `{"campaigns":[{"id":"b1","inviteKey":"b1","title":{"0":"Engineer","1":"Ingénieur"},"fullDescription":{"0":"<p>Build</p>","1":"<p>Construire</p>"},"language":0,"location":{"name":"Zürich"},"details":{"contract":{"type":"contractType_permanent"}}},{"id":"b2","inviteKey":"b2","title":"Engineer","description":"<p>Build</p>"}]}`)
		case "hirehive":
			page := r.URL.Query().Get("page")
			if r.URL.Query().Get("page_size") != "100" {
				w.WriteHeader(400)
				return
			}
			if page == "2" && mode == "partial" {
				w.WriteHeader(502)
				return
			}
			if page == "2" && mode == "late-reserved" {
				w.Header().Set("TDM-Reservation", "1")
			}
			fmt.Fprintf(w, `{"items":[{"id":%s,"hosted_url":"https://tenant.hirehive.com/engineer-%s/","title":"Engineer","description":{"html":"<p>Build</p>"},"location":"Zürich"}],"meta":{"has_next_page":%t}}`, page, page, page == "1")
		case "welcometothejungle":
			if r.URL.Path == "/1/indexes/*/queries" {
				var body map[string]any
				if r.Method != "POST" || json.NewDecoder(r.Body).Decode(&body) != nil || len(body) != 1 || r.Header.Get("X-Algolia-Api-Key") != ninthWTTJHeaders().Get("X-Algolia-Api-Key") {
					w.WriteHeader(400)
					return
				}
				fmt.Fprint(w, `{"results":[{"nbHits":3,"hits":[{"slug":"mirror","reference":"REF1","website":{"reference":"marketplace"}},{"slug":"engineer-1","reference":"REF1","website":{"reference":"tenant"}},{"slug":"engineer-2","reference":"REF2"}]}]}`)
				return
			}
			if !strings.Contains(r.URL.Path, "/jobs/") {
				fmt.Fprint(w, `{"organization":{"slug":"legacy-tenant"}}`)
				return
			}
			id := r.URL.Path[strings.LastIndex(r.URL.Path, "/")+1:]
			if id == "engineer-2" && mode == "partial" {
				w.WriteHeader(502)
				return
			}
			if id == "engineer-2" && mode == "late-reserved" {
				w.Header().Set("TDM-Reservation", "1")
			}
			if id == "engineer-2" && mode == "detail-gone" {
				w.WriteHeader(410)
				return
			}
			fmt.Fprintf(w, `{"job":{"slug":%q,"name":"Engineer","status":"published","archived_at":"2020-01-01","description":"<p>Build</p>","offices":[{"city":"Paris"}],"language":"fr","remote":"partial"}}`, id)
		case "computrabajo":
			page := 1
			if r.URL.Query().Get("pageNumber") == "2" {
				page = 2
			}
			if page == 2 && mode == "late-reserved" {
				w.Header().Set("TDM-Reservation", "1")
			}
			total := 21
			if page == 2 && (mode == "partial" || mode == "snapshot-recovery" && request == 2) {
				total = 22
			}
			fmt.Fprintf(w, `<section id="VacancySection"></section><div class="color-title font-3xl">%d ofertas de empleo</div><input id="hdn_PageSize" value="20"><input id="hdn_PageNumber" value="%d"><input id="hdn_isLast" value="%t">`, total, page, page == 2)
			start, end := 1, 20
			if page == 2 {
				start, end = 21, total
			}
			for id := start; id <= end; id++ {
				fmt.Fprintf(w, `<a class="card-vacancy" href="/Detail/%d">Job</a>`, id)
			}
		}
	}
}

func TestNinthProvidersWholeInventoryFailureAndPolicy(t *testing.T) {
	for _, provider := range []string{"beehire", "hirehive", "welcometothejungle", "computrabajo", "ycombinator"} {
		for _, mode := range []string{"success", "reserved", "partial", "redirect", "gone", "late-reserved", "retry", "detail-gone", "snapshot-recovery"} {
			if mode == "late-reserved" && (provider == "beehire" || provider == "ycombinator") || mode == "retry" && provider != "hirehive" && provider != "computrabajo" || mode == "detail-gone" && provider != "welcometothejungle" || mode == "snapshot-recovery" && provider != "computrabajo" {
				continue
			}
			t.Run(provider+"/"+mode, func(t *testing.T) {
				config := map[string]string{"crawler_type": provider, "board_url": ninthFixtureSource(provider), "metadata": ninthFixtureMetadata(provider), "monitor_needs_browser": "0"}
				o, err := api.NinthProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
				if err != nil {
					t.Fatal(err)
				}
				p := queue.GreenhouseMonitorProfile{Provider: provider, Profile: o.Profile(), Endpoint: o.ListingURL()}
				var requests, waits atomic.Int32
				client := verifiedClaimFixtureClient(t, ninthFixtureHandler(provider, mode, &requests))
				got, err := FetchNinthProvidersHTTP(context.Background(), client.client, p, config, func(ctx context.Context, delay time.Duration) error { waits.Add(1); return ctx.Err() })
				failure := mode != "success" && mode != "retry" && mode != "detail-gone" && mode != "snapshot-recovery"
				if failure {
					if err == nil || len(got.Jobs) != 0 {
						t.Fatal("incomplete inventory accepted", err, len(got.Jobs))
					}
					if strings.Contains(mode, "reserved") {
						var reservation *policy.Reservation
						if !errors.As(err, &reservation) {
							t.Fatal("publisher decision lost", err)
						}
					}
					if mode == "redirect" && requests.Load() != 1 && (provider != "hirehive" || requests.Load() != 3) {
						t.Fatal("foreign redirect fetched")
					}
					if mode == "gone" {
						var d *DiscoveryError
						if !errors.As(err, &d) || (d.Kind == "provider_gone") != (provider == "beehire" || provider == "hirehive" || provider == "computrabajo") {
							t.Fatal("board gone authority changed", err)
						}
					}
					return
				}
				want := 2
				if provider == "computrabajo" {
					want = 21
				}
				if mode == "detail-gone" {
					want = 1
				}
				if err != nil || len(got.Jobs) != want {
					t.Fatal("complete inventory differs", err, len(got.Jobs), want)
				}
				if provider == "beehire" && (len(got.Jobs[0].LocalizedTitles) != 2 || len(got.Jobs[0].LocalizationLocales) != 2) {
					t.Fatal("localized titles/locales dropped")
				}
				if mode == "retry" || mode == "snapshot-recovery" {
					if waits.Load() != 1 {
						t.Fatal("bounded retry changed", waits.Load())
					}
				}
			})
		}
	}
}
