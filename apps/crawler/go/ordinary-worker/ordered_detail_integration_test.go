package worker

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/redis/go-redis/v9"
)

func TestRealOrderedDetailSeparateAttemptsAndPartialPersistence(t *testing.T) {
	for _, primary := range []bool{true, false} {
		t.Run(fmt.Sprint(primary), func(t *testing.T) {
			metadata := `{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","fields":["description"],"config":{"steps":[{"tag":"p","field":"description","html":true}]}}}}`
			f, a, claim := independentDetailOwnedFixture(t, metadata, "")
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET description_r2_hash=123 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			processor := richPipelinePreparer(t, f).Processor
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				if primary && calls == 1 {
					fmt.Fprint(w, nativeJSONLDHTML)
				} else {
					fmt.Fprint(w, `<p>Follow-up description with Python experience.</p>`)
				}
			})
			result, err := RunDetail(ctx, a, claim, client, processor, circuits)
			if err != nil || !result.Settled || calls < 1 {
				t.Fatal("primary attempt failed", err, result)
			}
			if f.r.HGet(ctx, "scrape:"+f.original, "scrape_step").Val() != "1" || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 1 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("next separately fenced stage absent")
			}
			if f.r.HGet(ctx, "scrape:"+f.original, "description_r2_hash").Val() != "123" || f.r.HGet(ctx, "scrape:"+f.original, "scrape_interval_hours").Val() != "24" {
				t.Fatal("original next-stage cache metadata differs")
			}
			var title string
			var failures int
			if err := f.pg.QueryRow(ctx, "SELECT titles[1],scrape_failures FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title, &failures); err != nil || failures != 0 {
				t.Fatal("primary effects differ", err)
			}
			want := "Original"
			if primary {
				want = "Senior Software Engineer"
			}
			if title != want {
				t.Fatal("primary title differs", title)
			}
			primaryCalls := calls
			next, err := a.Claim(ctx, queue.Simple)
			if err != nil || next == nil || next.RecoveredReceipt() != nil || next.Descriptor().Config["scrape_step"] != "1" {
				t.Fatal("next stage claim failed", err)
			}
			result, err = RunDetail(ctx, a, next, client, processor, circuits)
			if err != nil || !result.Settled || calls != primaryCalls+1 {
				t.Fatal("partial stage failed", err, result)
			}
			var html string
			if err := f.pg.QueryRow(ctx, "SELECT p.titles[1],p.scrape_failures,d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid", f.original).Scan(&title, &failures, &html); err != nil || title != want || failures != 0 || html == "" {
				t.Fatal("partial persistence replaced retained values", err, title, failures, html)
			}
			if f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 0 || f.r.HGet(ctx, "scrape:"+f.original, "scrape_step").Val() != "1" {
				t.Fatal("final stage reset legacy retained step")
			}
		})
	}
}

func TestRealOrderedDetailCommitBeforeAckRecovery(t *testing.T) {
	for _, missingTitle := range []bool{false, true} {
		t.Run(fmt.Sprint(missingTitle), func(t *testing.T) {
			metadata := `{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"steps":[{"tag":"p","field":"description","html":true}]}}}}`
			f, a, claim := independentDetailOwnedFixture(t, metadata, "")
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET description_r2_hash=123 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			detail, err := a.ReadWorkdayDetail(ctx, claim)
			if err != nil {
				t.Fatal(err)
			}
			values := map[string]any{"title": "Migration Engineer", "description": "<p>Python experience</p>"}
			if missingTitle {
				values = map[string]any{}
			}
			receipt, err := PersistDetailContent(ctx, a, detail, richPipelinePreparer(t, f).Processor, values)
			if err != nil || receipt == nil {
				t.Fatal("terminal commit failed", err)
			}
			// A legitimate drain can change the canonical uploaded hash after
			// commit. Recovery must publish the hash retained by the original step.
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET description_r2_hash=999 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			var before string
			if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(p)::text FROM job_posting p WHERE id=$1::uuid", f.original).Scan(&before); err != nil {
				t.Fatal(err)
			}
			member := "scrape|example.com|" + f.original
			if err := f.r.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: member}).Err(); err != nil {
				t.Fatal(err)
			}
			body, err := os.ReadFile("../../src/lua/reap_expired.lua")
			if err != nil {
				t.Fatal(err)
			}
			if err := queue.WithOrdinaryLeaseRetirement(ctx, f.pg, func(ctx context.Context) error {
				now, err := f.r.Time(ctx).Result()
				if err != nil {
					return err
				}
				at := float64(now.UnixMicro()) / 1e6
				return f.r.Eval(ctx, string(body), nil, "simple", at, 10, 3, at, "guarded").Err()
			}); err != nil {
				t.Fatal("supported reap failed", err)
			}
			recovered, err := a.Claim(ctx, queue.Simple)
			if err != nil || recovered == nil || recovered.RecoveredReceipt() == nil {
				t.Fatal("committed handoff not recovered", err)
			}
			if err := a.Settle(ctx, recovered, recovered.RecoveredReceipt()); err != nil {
				t.Fatal("recovery publication failed", err)
			}
			var after string
			if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(p)::text FROM job_posting p WHERE id=$1::uuid", f.original).Scan(&after); err != nil || before != after {
				t.Fatal("recovery repeated canonical effects", err)
			}
			if f.r.HGet(ctx, "scrape:"+f.original, "scrape_step").Val() != "1" || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 1 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("recovered publication effects differ")
			}
			if f.r.HGet(ctx, "scrape:"+f.original, "description_r2_hash").Val() != "123" || f.r.HGet(ctx, "scrape:"+f.original, "scrape_interval_hours").Val() != "24" {
				t.Fatal("recovery lost original handoff cache metadata")
			}
			// Ready time is the original committed database clock, including the
			// missing-primary path whose canonical recurring due was already past.
			if f.r.ZScore(ctx, "ft_scrapes_simple:example.com", f.original).Val() > float64(time.Now().UnixMicro())/1e6 {
				t.Fatal("handoff shifted into future")
			}
		})
	}
}

func TestRealOrderedDetailPolicyAndTransportDoNotAdvance(t *testing.T) {
	for _, mode := range []string{"reserved", "503", "empty-final"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}],"fallback":{"type":"dom","config":{"steps":[{"tag":"p","field":"description","html":true}]}}}}`
			f, a := independentDetailOwnedSetup(t, metadata, "", queue.Simple)
			ctx := context.Background()
			if mode == "empty-final" {
				if err := f.r.HSet(ctx, "scrape:"+f.original, "scrape_step", "1").Err(); err != nil {
					t.Fatal(err)
				}
			}
			claim, err := a.Claim(ctx, queue.Simple)
			if err != nil || claim == nil {
				t.Fatal("policy fixture missing", err)
			}
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if mode == "reserved" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(410)
				}
				if mode == "503" {
					w.WriteHeader(503)
				}
				fmt.Fprint(w, `<html>no content</html>`)
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || !result.Settled {
				t.Fatal("policy terminal failed", err, result)
			}
			var failures int
			var reserved bool
			if err := f.pg.QueryRow(ctx, "SELECT scrape_failures,tdm_reserved FROM job_posting WHERE id=$1::uuid", f.original).Scan(&failures, &reserved); err != nil {
				t.Fatal(err)
			}
			if mode == "reserved" && (!reserved || failures != 0) || mode == "503" && failures != 1 || mode == "empty-final" && failures != 0 {
				t.Fatal("legacy budget/policy differs", failures, reserved)
			}
			if f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 0 {
				t.Fatal("failure or final stage advanced chain")
			}
		})
	}
}

func TestRealOrderedDetailSelectsSealedTransportPerStage(t *testing.T) {
	metadata := `{"scraper_type":"dom","scraper_config":{"proxy":true,"steps":[{"tag":"h1","field":"title"}],"fallback":{"type":"json-ld","fields":["date_posted"],"config":{}}}}`
	f, a, claim := independentDetailOwnedFixture(t, metadata, "")
	ctx := context.Background()
	if !a.RequiresProxyHTTP() {
		t.Fatal("ordered plan did not require configured proxy")
	}
	processor := richPipelinePreparer(t, f).Processor
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls == 1 {
			fmt.Fprint(w, `<h1>Primary Proxy Engineer</h1>`)
		} else {
			fmt.Fprint(w, nativeJSONLDHTML)
		}
	})
	proxy, err := runtimeClaimUsesProxy(ctx, a, claim)
	if err != nil || !proxy {
		t.Fatal("primary proxy selector differs", err)
	}
	if result, err := RunDetail(ctx, a, claim, client, processor, circuits); err == nil || result.Settled || calls != 0 {
		t.Fatal("primary fetched with direct client")
	}
	result, err := RunDetail(ctx, a, claim, credentialedProxyFixture(t, client), processor, circuits)
	if err != nil || !result.Settled || calls != 1 {
		t.Fatal("proxy primary failed", err, result)
	}
	next, err := a.Claim(ctx, queue.Simple)
	if err != nil || next == nil {
		t.Fatal(err)
	}
	proxy, err = runtimeClaimUsesProxy(ctx, a, next)
	if err != nil || proxy {
		t.Fatal("fallback reused primary proxy selector", err)
	}
	result, err = RunDetail(ctx, a, next, client, processor, circuits)
	if err != nil || !result.Settled || calls != 2 {
		t.Fatal("direct fallback failed", err, result)
	}
	var title string
	if err := f.pg.QueryRow(ctx, "SELECT titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title); err != nil || title != "Senior Software Engineer" {
		t.Fatal("inert fallback.fields was applied as a mask", err, title)
	}
}
