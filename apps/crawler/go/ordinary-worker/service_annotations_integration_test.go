package worker

import (
	"context"
	"encoding/json"
	"fmt"
	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"testing"
)

func sharedAnnotationFixtureMetadata(t *testing.T, raw string, annotated bool) string {
	t.Helper()
	if !annotated {
		return raw
	}
	var md map[string]any
	if json.Unmarshal([]byte(raw), &md) != nil {
		t.Fatal("metadata")
	}
	md["defaults"] = map[string]any{"title": "must not replace actual title", "locations": []string{"must not replace actual location"}}
	md["rescrape_policy"] = "never"
	b, e := json.Marshal(md)
	if e != nil {
		t.Fatal(e)
	}
	return string(b)
}

func TestRealSharedServiceAnnotationsPreserveInventoryAndTerminalEffects(t *testing.T) {
	t.Run("dom", func(t *testing.T) { realDOMTransportCases(t, false, true) })
	t.Run("sitemap", func(t *testing.T) { realSitemapTransportCases(t, false, true) })
	t.Run("api", func(t *testing.T) {
		realAPIRichTransportCases(t, false, true)
		realAPIFailureTransportCases(t, false, true)
	})
	t.Run("proxy-api", func(t *testing.T) {
		realAPIRichTransportCases(t, true, true)
		realAPIFailureTransportCases(t, true, true)
	})
}

func TestRealSharedNeverRescrapeFirstRelistedAndSuccessfulDetail(t *testing.T) {
	t.Run("first-and-relisted", func(t *testing.T) { realAPIEnrichmentTransportCases(t, true) })
	t.Run("successful-detail", func(t *testing.T) {
		md := sharedAnnotationFixtureMetadata(t, `{"scraper_type":"dom","render":true,"scraper_config":{"steps":[{"tag":"h1","field":"title"},{"tag":"p","field":"description","html":true}]}}`, true)
		f, a, claim := independentDetailOwnedFixture(t, md, "")
		ctx := context.Background()
		client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, `<h1>Engineer</h1><p>Build systems</p>`) })
		circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
		if e != nil {
			t.Fatal(e)
		}
		result, e := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
		if e != nil || result == nil || !result.Settled {
			t.Fatal("detail not settled", e)
		}
		var active, due bool
		if e := f.pg.QueryRow(ctx, "SELECT is_active,next_scrape_at IS NOT NULL FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &due); e != nil {
			t.Fatal(e)
		}
		if !active || due || f.r.ZCard(ctx, "inflight:simple").Val() != 0 || len(f.r.Keys(ctx, "ft_scrapes_*").Val()) != 0 {
			t.Fatal("never-rescrape success retained future detail work")
		}
	})
}

func TestRealSharedBrowserURLInventorySchedulesFirstAndRelistedDetails(t *testing.T) {
	for _, mode := range []string{"new", "relisted"} {
		t.Run(mode, func(t *testing.T) {
			md := sharedAnnotationFixtureMetadata(t, `{"browser":true,"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","scraper_type":"json-ld"}`, true)
			f := privateRichPipelineFixture(t, "api_sniffer", md, queue.Browser, queue.Simple)
			ctx := context.Background()
			source := "https://example.com/job/" + f.company + "/browser-url"
			if mode == "relisted" {
				if _, e := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,is_active=false,next_scrape_at=NULL,description_r2_hash=123 WHERE id=$1::uuid", f.original, source); e != nil {
					t.Fatal(e)
				}
			}
			claim, e := f.a.Claim(ctx, queue.Browser)
			if e != nil || claim == nil {
				t.Fatal(e)
			}
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
				// Use the exact metadata crossing the renderer RPC boundary.
				options, e := api.BrowserReplayOptionsFromMetadata(c["board_url"], c["metadata"])
				if e != nil {
					return RichDiscovery{}, e
				}
				inventory, e := api.DiscoverBrowserReplay(ctx, options, func(context.Context, api.Request) (*api.Document, error) {
					return api.Decode([]byte(fmt.Sprintf(`{"jobs":[{"url":%q,"title":"Must remain a URL until detail scrape"}]}`, source)))
				}, pythonJoinURL, false)
				if e != nil {
					return RichDiscovery{}, e
				}
				raw, _ := json.Marshal(inventory)
				return parseAPIReplayResponse(p, replay.Response{Protocol: replay.Protocol, RequestID: p.EffectiveConfigSHA256, ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success", Inventory: raw})
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("browser used worker direct HTTP") })
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("browser URL inventory not settled", e)
			}
			var id string
			var active, due bool
			if e := f.pg.QueryRow(ctx, "SELECT id::text,is_active,next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&id, &active, &due); e != nil {
				t.Fatal(e)
			}
			if !active || !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val()+f.r.ZCard(ctx, "scrapes_simple:example.com").Val() != 1 {
				t.Fatal("browser URL inventory lost first/relisted detail work", active, due, f.r.HGet(ctx, "scrape:"+id, "source_url").Val(), f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val(), f.r.ZCard(ctx, "scrapes_simple:example.com").Val())
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer", queue.Browser)
		})
	}
}
