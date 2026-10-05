package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

const nativeEmbeddedHTML = `<html><script id="__NEXT_DATA__">{"job":{"title":"Senior Software Engineer","locations":["Zurich"],"employment":"Full-time","description":"<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>","responsibilities":["Build reliable services."]}}</script></html>`

func TestRealEmbeddedDetailPreservesCanonicalMaskPolicyAndRecurringIntent(t *testing.T) {
	for _, mode := range []string{"full", "description", "empty_backfill", "defaults", "variable", "empty_payload", "404", "503", "header_reserved", "meta_reserved", "redirect"} {
		t.Run(mode, func(t *testing.T) {
			options := `{"path":"job","fields":{"title":"title","description":"description","locations":"locations","employment_type":"employment","responsibilities":"responsibilities"}}`
			if mode == "description" || mode == "empty_backfill" {
				options = strings.TrimSuffix(options, "}") + `,"enrich":["description"]}`
			}
			if mode == "defaults" {
				options = `{"path":"job","fields":{"title":"title","description":"description"},"defaults":{"locations":["Zurich"],"employment_type":"Full-time"}}`
			}
			provider := "nextdata"
			if mode == "variable" {
				provider = "embedded"
				options = strings.TrimSuffix(options, "}") + `,"variable":"POSITION_DATA"}`
			}
			f, a, claim := independentDetailOwnedFixture(t, `{"scraper_type":"`+provider+`","scraper_config":`+options+`}`, "https://careers.example.net/job/123")
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Monitor title'],employment_type='part_time',location_ids=ARRAY[2] WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			if mode == "empty_backfill" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY[]::text[],employment_type=NULL,location_ids=ARRAY[]::integer[] WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
			}
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Host != "careers.example.net" || (r.URL.Path != "/job/123" && r.URL.Path != "/final/123") {
					t.Error("wrong canonical embedded resource")
				}
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				body := nativeEmbeddedHTML
				switch mode {
				case "404":
					w.WriteHeader(404)
					return
				case "503":
					w.WriteHeader(503)
					return
				case "header_reserved":
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "https://careers.example.net/policy")
					w.WriteHeader(503)
					return
				case "meta_reserved":
					body = `<meta name="tdm-reservation" content="1">` + nativeEmbeddedHTML
				case "empty_payload":
					body = `<script id="__NEXT_DATA__">{}</script>`
				case "variable":
					body = `<script>const POSITION_DATA=` + strings.TrimSuffix(strings.TrimPrefix(nativeEmbeddedHTML, `<html><script id="__NEXT_DATA__">`), `</script></html>`) + `;</script>`
				case "redirect":
					if calls == 1 {
						w.Header().Set("Location", "/final/123")
						w.WriteHeader(302)
						return
					}
				}
				fmt.Fprint(w, body)
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			status := "succeeded"
			if mode == "empty_payload" || mode == "404" || mode == "503" {
				status = "failed"
			}
			reserved := mode == "header_reserved" || mode == "meta_reserved"
			if reserved {
				status = "publisher_reserved"
			}
			wantCalls := 1
			if mode == "redirect" {
				wantCalls = 2
			}
			if err != nil || result == nil || !result.Settled || result.Cycle.Status != status || calls != wantCalls || result.HTTP.Requests != int64(wantCalls) {
				t.Fatal("embedded detail settlement differs", result, err, calls)
			}
			var title, employment string
			var due time.Time
			var sqlReserved, active bool
			var failures int
			if err := f.pg.QueryRow(ctx, "SELECT titles[1],employment_type,next_scrape_at,tdm_reserved,is_active,scrape_failures FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title, &employment, &due, &sqlReserved, &active, &failures); err != nil {
				t.Fatal(err)
			}
			wantTitle, wantEmployment := "Senior Software Engineer", "full_time"
			if status != "succeeded" || mode == "description" {
				wantTitle, wantEmployment = "Monitor title", "part_time"
			}
			wantFailures := 0
			if status == "failed" {
				wantFailures = 1
			}
			if title != wantTitle || employment != wantEmployment || !active || sqlReserved != reserved || failures != wantFailures {
				t.Fatal("embedded canonical mask/liveness differs", title, employment, sqlReserved, active, failures)
			}
			score, err := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
			if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("embedded recurring deadline/lease differs", err)
			}
			if status == "succeeded" {
				var body string
				var uploaded bool
				var locations []int32
				if err := f.pg.QueryRow(ctx, "SELECT d.html,d.r2_uploaded,p.location_ids FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid", f.original).Scan(&body, &uploaded, &locations); err != nil || !strings.Contains(body, "Salary CHF") || uploaded || fmt.Sprint(locations) != "[2]" {
					t.Fatal("embedded content staging differs", err)
				}
				if mode != "defaults" && !strings.Contains(body, "Build reliable services.") {
					t.Fatal("embedded structured extras omitted")
				}
			}
		})
	}
}
