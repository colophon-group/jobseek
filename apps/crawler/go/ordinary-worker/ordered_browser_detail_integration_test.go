package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestRealOrderedBrowserDetailSeparateHTTPAndRenderedAttempts(t *testing.T) {
	for _, mode := range []string{"primary-content", "primary-empty", "fallback-reserved", "canonical-reserved", "fallback-empty", "fallback-empty-proxy"} {
		t.Run(mode, func(t *testing.T) {
			emptyFallback := mode == "fallback-empty" || mode == "fallback-empty-proxy"
			metadata := `{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","fields":["description"],"config":{"render":true,"steps":[{"tag":"p","field":"description","html":true}]}}}}`
			if emptyFallback {
				metadata = `{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"render":true,"wait":"load"}}}}`
				if mode == "fallback-empty-proxy" {
					metadata = strings.Replace(metadata, `"wait":"load"`, `"wait":"load","proxy":true`, 1)
				}
			}
			f, a := independentDetailOwnedProviderSetup(t, "dom", metadata, "", queue.Browser)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET description_r2_hash=123,tdm_reserved=$2 WHERE id=$1::uuid", f.original, mode == "canonical-reserved"); err != nil {
				t.Fatal(err)
			}
			claim, err := a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("original browser claim unavailable", err)
			}
			calls, renders := 0, 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				if mode == "primary-empty" {
					fmt.Fprint(w, "<html></html>")
				} else {
					fmt.Fprint(w, nativeJSONLDHTML)
				}
			})
			renderer := heldRenderedDetail(func(_ context.Context, profile queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
				renders++
				if profile.Profile != "dom.rendered-detail/v1" || profile.SourceURL != claim.Descriptor().Config["source_url"] {
					t.Error("fallback parser/source changed")
				}
				if mode == "fallback-reserved" {
					return nil, &policy.Reservation{URL: profile.SourceURL, Source: "header"}, nil
				}
				return map[string]any{"description": "<p>Rendered follow-up with Python experience.</p>"}, nil, nil
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			processor := richPipelinePreparer(t, f).Processor
			result, err := RunDetail(ctx, a, claim, client, processor, circuits, renderer)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("primary attempt failed", err)
			}
			if mode == "canonical-reserved" {
				if calls != 0 || renders != 0 || f.r.HGet(ctx, "scrape:"+f.original, "scrape_step").Val() == "1" {
					t.Fatal("canonical reservation fetched or advanced fallback")
				}
				return
			}
			if calls < 1 || renders != 0 || f.r.HGet(ctx, "scrape:"+f.original, "scrape_step").Val() != "1" || f.r.ZCard(ctx, "ft_scrapes_browser:example.com").Val() != 1 || f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 0 {
				t.Fatal("HTTP primary changed original browser handoff")
			}
			if f.r.HGet(ctx, "scrape:"+f.original, "description_r2_hash").Val() != "123" || f.r.HGet(ctx, "scrape:"+f.original, "scrape_interval_hours").Val() != "24" {
				t.Fatal("original handoff metadata changed")
			}
			next, err := a.Claim(ctx, queue.Browser)
			if err != nil || next == nil || next.Descriptor().Config["scrape_step"] != "1" {
				t.Fatal("separate browser fallback claim unavailable", err)
			}
			before := calls
			if emptyFallback {
				usesProxy, err := runtimeClaimUsesProxy(ctx, a, next)
				if err != nil || usesProxy {
					t.Fatal("no-fetch fallback requires proxy startup or a live endpoint", err)
				}
			}
			result, err = RunDetail(ctx, a, next, client, processor, circuits, renderer)
			wantRenders := 1
			if emptyFallback {
				wantRenders = 0
			}
			if err != nil || result == nil || !result.Settled || calls != before || renders != wantRenders {
				t.Fatal("fallback used wrong transport or failed settlement", err)
			}
			var title string
			var failures int
			var reserved bool
			var due *time.Time
			var lease *time.Time
			if err := f.pg.QueryRow(ctx, "SELECT titles[1],scrape_failures,tdm_reserved,next_scrape_at,leased_until FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title, &failures, &reserved, &due, &lease); err != nil || (failures != 0 && !emptyFallback) || lease != nil {
				t.Fatal("fallback canonical failure/lease differs", err)
			}
			want := "Senior Software Engineer"
			if mode == "primary-empty" {
				want = "Original"
			}
			if emptyFallback {
				if failures != 0 || title != want || reserved || result.HTTP.Requests != 0 || result.Cycle.Status != "succeeded" {
					t.Fatal("empty fallback fetched or changed original partial-save behavior")
				}
				return
			}
			if title != want || reserved != (mode == "fallback-reserved") {
				t.Fatal("fallback replaced retained title or lost publisher reservation")
			}
			if !reserved {
				var html string
				if err := f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid", f.original).Scan(&html); err != nil || !strings.Contains(html, "Rendered follow-up") {
					t.Fatal("fallback description not persisted", err)
				}
				if score, err := f.r.ZScore(ctx, "scrapes_browser:example.com", f.original).Result(); err != nil || due == nil || score != float64(due.UnixMicro())/1e6 {
					t.Fatal("final canonical browser deadline differs", err)
				}
			}
			if f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.ZCard(ctx, "ft_scrapes_browser:example.com").Val() != 0 || f.r.HGet(ctx, "scrape:"+f.original, "scrape_step").Val() != "1" {
				t.Fatal("final settlement reset step or retained inflight work")
			}
		})
	}
}
