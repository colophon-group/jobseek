package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type lifepointHTTPTestReader struct {
	wait func(context.Context, time.Duration) error
}

func (r lifepointHTTPTestReader) FetchMonitor(ctx context.Context, p queue.GreenhouseMonitorProfile, config map[string]string, client *http.Client) (RichDiscovery, error) {
	return discoverQualifiedLifepointDOMHTTPMonitorWithWait(ctx, client, p, config, r.wait)
}

func TestRealQualifiedLifepointHTTPMonitorKeepsCompletePagesAndCanonicalGuards(t *testing.T) {
	for _, mode := range []string{"success", "redirect", "empty", "missing-token", "ambiguous-token", "root-policy", "redirect-policy", "api-policy-error", "malformed-page", "foreign-link", "first-rate", "later-rate", "repeated-page", "no-progress", "foreign-redirect", "second-redirect"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "dom", `{"render":true,"actions":[{"action":"wait","ms":1000}],"url_filter":"^https?://jobs[.]lifepointhealth[.]net/job/.+/$","scraper_type":"json-ld"}`, queue.Browser, queue.Simple)
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
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
			qualifiedLifepointDOMHTTPMonitors[p.EffectiveConfigSHA256] = domHTTPMonitorRoute{"example.com", "/careers"}
			defer delete(qualifiedLifepointDOMHTTPMonitors, p.EffectiveConfigSHA256)
			changed := map[string]string{}
			for k, v := range config {
				changed[k] = v
			}
			changed["check_interval_minutes"] = "123"
			foreign := p
			foreign.Endpoint = "https://foreign.example/careers"
			if qualifiedLifepointDOMHTTPMonitor(p, changed) || qualifiedLifepointDOMHTTPMonitor(foreign, config) {
				t.Fatal("changed original config or source acquired the replacement")
			}
			const token = "12345.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
			const first = `<a href="https://jobs.lifepointhealth.net/job/qualified-a/">Engineer A</a>`
			calls, pages, waits := 0, 0, 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Header.Get("Authorization") != "" {
					t.Error("public read changed method or acquired authorization")
				}
				w.Header().Set("Content-Type", "text/html")
				if r.URL.Path == "/careers" {
					if mode == "root-policy" || mode == "redirect-policy" {
						w.Header().Set("TDM-Reservation", "1")
					}
					if mode == "foreign-redirect" {
						w.Header().Set("Location", "https://foreign.example/careers")
						w.WriteHeader(301)
						return
					}
					if (mode == "redirect" || mode == "redirect-policy" || mode == "second-redirect") && r.Host == "example.com" {
						w.Header().Set("Location", "https://jobs.lifepointhealth.net/careers?filter%5Bcategory%5D%5B%5D=Physician&filter%5Bcategory%5D%5B%5D=Advanced+Practice+Provider")
						w.WriteHeader(301)
						return
					}
					if mode == "second-redirect" {
						w.Header().Set("Location", "https://jobs.lifepointhealth.net/careers?filter%5Bcategory%5D%5B%5D=Physician&filter%5Bcategory%5D%5B%5D=Advanced+Practice+Provider")
						w.WriteHeader(302)
						return
					}
					if mode == "missing-token" {
						fmt.Fprint(w, `<html>Incomplete public page</html>`)
						return
					}
					fmt.Fprintf(w, `<script>var publicSettings={"calcToken":%q};</script>`, token)
					if mode == "ambiguous-token" {
						fmt.Fprint(w, `<script>var changedSettings={"calcToken":"12345.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"};</script>`)
					}
					return
				}
				if r.Host != "jobs.lifepointhealth.net" || r.URL.Path != "/calc-results/" || r.URL.Query().Get("ctok") != token || r.Header.Get("X-Requested-With") != "XMLHttpRequest" {
					t.Error("result query escaped its public origin, nonce or protocol")
				}
				pages++
				page := r.URL.Query().Get("mypage")
				if mode == "api-policy-error" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(403)
					return
				}
				if mode == "first-rate" || mode == "later-rate" && page == "1" {
					w.WriteHeader(429)
					return
				}
				if mode == "empty" {
					return
				}
				if mode == "malformed-page" {
					fmt.Fprint(w, `<title>Unexpected result format</title>`)
					return
				}
				if mode == "foreign-link" {
					fmt.Fprint(w, `<a href="https://foreign.example/job/rejected/">Foreign job</a>`)
					return
				}
				switch page {
				case "0":
					fmt.Fprint(w, first)
				case "1":
					if mode == "repeated-page" {
						fmt.Fprint(w, first)
					} else if mode == "no-progress" {
						fmt.Fprint(w, first+`<p>Different markup, same jobs</p>`)
					} else {
						fmt.Fprint(w, first+`<a href="https://jobs.lifepointhealth.net/job/qualified-b/">Engineer B</a>`)
					}
				case "2":
				default:
					t.Error("reader exceeded the terminal page")
				}
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			reader := lifepointHTTPTestReader{wait: func(ctx context.Context, delay time.Duration) error {
				waits++
				if delay != time.Second {
					t.Error("public result reads lost their pacing")
				}
				return ctx.Err()
			}}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits, reader)
			if err != nil || result == nil || !result.Settled || calls == 0 || pages != waits {
				t.Fatal("public result query did not settle or lost pacing", err)
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
			case "success", "redirect":
				if count != 3 || active || missing != 4 || failures != 0 || pages != 3 {
					t.Fatal("complete consecutive pages changed canonical inventory", count, active, missing, failures, pages)
				}
			case "empty":
				if count != 1 || !active || missing != 3 || failures != 0 {
					t.Fatal("empty result bypassed the shared drop guard")
				}
			case "root-policy", "redirect-policy", "api-policy-error":
				if !reserved || !active || missing != 3 || count != 1 || failures != 0 || result.Cycle.Status != "publisher_reserved" {
					t.Fatal("publisher reservation changed canonical inventory")
				}
			default:
				if !active || missing != 3 || count != 1 || failures == 0 {
					t.Fatal("failed or partial result mutated canonical jobs", count, active, missing, failures)
				}
			}
			if f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.ZCard(ctx, "monitors_simple:dom").Val() != 0 {
				t.Fatal("original queue lane or settlement changed")
			}
		})
	}
}
