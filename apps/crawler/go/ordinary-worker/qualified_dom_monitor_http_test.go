package worker

import (
	"context"
	"net/http"
	"testing"
	"time"

	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealQualifiedDOMHTTPMonitorPreservesLifecycleAndPolicy(t *testing.T) {
	for _, mode := range []string{"success", "header", "meta", "403", "404", "challenge"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "dom", `{"render":true,"link_selector":"a.job","actions":[{"action":"wait","ms":8000}],"scraper_type":"json-ld"}`, queue.Browser, queue.Simple)
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("browser claim unavailable", err)
			}
			config := claim.Descriptor().Config
			p, err := queue.InspectRichMonitor(f.board, config)
			if err != nil {
				t.Fatal(err)
			}
			qualifiedDOMHTTPMonitors[p.EffectiveConfigSHA256] = domHTTPMonitorRoute{"example.com", "/careers"}
			defer delete(qualifiedDOMHTTPMonitors, p.EffectiveConfigSHA256)
			changed := map[string]string{}
			for k, v := range config {
				changed[k] = v
			}
			changed["check_interval_minutes"] = "123"
			if qualifiedDOMHTTPMonitor(p, changed) {
				t.Fatal("changed original config granted HTTP transport")
			}
			foreign := p
			foreign.Endpoint = "https://foreign.example/careers"
			if qualifiedDOMHTTPMonitor(foreign, config) {
				t.Fatal("foreign source granted HTTP transport")
			}
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Host != "example.com" || r.URL.Path != "/careers" {
					t.Error("request outside bound public source")
				}
				w.Header().Set("Content-Type", "text/html")
				body := `<a class="job" href="/jobs/qualified">Engineer</a>`
				switch mode {
				case "header":
					w.Header().Set("TDM-Reservation", "1")
				case "meta":
					body = `<meta name="tdm-reservation" content="1">`
				case "403":
					w.WriteHeader(403)
				case "404":
					w.WriteHeader(404)
				case "challenge":
					body = `<title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div>`
				}
				w.Write([]byte(body))
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			// A renderer client with no network configuration proves the qualified
			// dispatch never invokes the browser or waits for a renderer slot.
			renderer := &NativeRenderedDetails{client: &lp.Client{}}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits, renderer)
			if err != nil || result == nil || !result.Settled || calls < 1 {
				t.Fatal("HTTP monitor did not settle", err)
			}
			var active, reserved bool
			var missing, count, failures int
			if err = f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &count); err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "success":
				if count != 2 || active || missing != 4 || failures != 0 {
					t.Fatal("complete native inventory lifecycle changed")
				}
			case "header", "meta":
				if !reserved || !active || missing != 3 || count != 1 || failures != 0 || result.Cycle.Status != "publisher_reserved" {
					t.Fatal("publisher reservation changed inventory")
				}
			default:
				if !active || missing != 3 || count != 1 {
					t.Fatal("failed fetch was treated as empty inventory")
				}
			}
			if f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.ZCard(ctx, "monitors_simple:dom").Val() != 0 {
				t.Fatal("original browser queue settlement changed")
			}
		})
	}
}
