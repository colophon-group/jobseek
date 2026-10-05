package worker

import (
	"context"
	"encoding/json"
	"fmt"
	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestRealHTTPAPIDetailPreservesRequestsCanonicalWritesPolicyAndRetry(t *testing.T) {
	for _, mode := range []string{"full", "auth", "description", "redirect", "404", "410", "503", "403", "malformed", "empty", "auth_missing", "auth_reserved", "header_reserved", "503_reserved"} {
		t.Run(mode, func(t *testing.T) {
			options := `{"api_url":"https://api.example.net/detail/{item_id}","method":"POST","post_body":"{\"id\":\"{item_id}\"}","url_pattern":"[?&]itemId=(?P<item_id>[^&#]+)","json_path":"job","fields":{"title":"title","description":"description","locations":"locations","employment_type":"employment","responsibilities":"responsibilities","base_salary":"salary"}}`
			auth := strings.HasPrefix(mode, "auth")
			if auth {
				options = strings.TrimSuffix(options, "}") + `,"auth_request":{"api_url":"https://auth.example.net/session","json_path":"result","header_fields":{"Authorization":"token"}}}`
			}
			if mode == "description" {
				options = strings.TrimSuffix(options, "}") + `,"enrich":["description","date_posted","job_location_type"]}`
			}
			f, a, claim := independentDetailOwnedFixture(t, `{"scraper_type":"api_sniffer","scraper_config":`+options+`}`, "https://careers.example.net/job?itemId=123")
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Monitor title'],employment_type='part_time',location_ids=ARRAY[2] WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Host == "auth.example.net" {
					if r.Method != "GET" || r.URL.Path != "/session" {
						t.Error("wrong auth request")
					}
					if mode == "auth_reserved" {
						w.Header().Set("TDM-Reservation", "1")
					}
					if mode == "auth_missing" {
						fmt.Fprint(w, `{"result":{}}`)
					} else {
						http.SetCookie(w, &http.Cookie{Name: "operation", Value: "fixture", Path: "/"})
						fmt.Fprint(w, `{"result":{"token":"Bearer fixture"}}`)
					}
					return
				}
				body, _ := io.ReadAll(r.Body)
				if r.Host != "api.example.net" || (r.URL.Path != "/detail/123" && !(mode == "redirect" && r.URL.Path == "/final/123")) || r.Method != "POST" || string(body) != `{"id":"123"}` || r.Header.Get("Content-Type") != "application/json" {
					t.Error("wrong bound API request")
				}
				if auth && r.Header.Get("Authorization") != "Bearer fixture" {
					t.Error("missing auth response header")
				}
				if mode == "redirect" && calls == 1 {
					w.Header().Set("Location", "/final/123")
					w.WriteHeader(307)
					return
				}
				switch mode {
				case "404":
					w.WriteHeader(404)
					return
				case "410":
					w.WriteHeader(410)
					return
				case "403":
					w.WriteHeader(403)
					return
				case "503", "503_reserved":
					if mode == "503_reserved" {
						w.Header().Set("TDM-Reservation", "1")
					}
					w.WriteHeader(503)
					return
				case "header_reserved":
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "https://api.example.net/policy")
				case "malformed":
					fmt.Fprint(w, "{")
					return
				case "empty":
					fmt.Fprint(w, `{"job":null}`)
					return
				}
				fmt.Fprint(w, `{"job":{"title":"Senior Software Engineer","description":"<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>","locations":["Zurich"],"employment":"Full-time","salary":"CHF 100000 - 120000 per year","responsibilities":["Build reliable services."]}}`)
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			status, callsWant, reserved := "succeeded", 1, false
			if auth {
				callsWant = 2
			}
			switch mode {
			case "redirect":
				callsWant = 2
			case "404", "410", "403", "empty":
				status = "failed"
			case "503", "malformed", "503_reserved":
				status = "failed"
				callsWant = 3
			case "auth_missing":
				status = "failed"
				callsWant = 1
			case "auth_reserved", "header_reserved":
				status = "publisher_reserved"
				callsWant = 1
				reserved = true
			}
			if err != nil || result == nil || !result.Settled || result.Cycle.Status != status || calls != callsWant || result.HTTP.Requests != int64(callsWant) || result.HTTP.Responses != int64(callsWant) {
				t.Fatal("API detail settlement differs", result, err, calls)
			}
			var title, employment string
			var due time.Time
			var sqlReserved, active bool
			var failures int
			if err := f.pg.QueryRow(ctx, "SELECT titles[1],employment_type,next_scrape_at,tdm_reserved,is_active,scrape_failures FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title, &employment, &due, &sqlReserved, &active, &failures); err != nil {
				t.Fatal(err)
			}
			wantTitle, wantEmployment, wantFailures := "Senior Software Engineer", "full_time", 0
			if status != "succeeded" || mode == "description" {
				wantTitle, wantEmployment = "Monitor title", "part_time"
			}
			if status == "failed" {
				wantFailures = 1
			}
			if title != wantTitle || employment != wantEmployment || !active || sqlReserved != reserved || failures != wantFailures {
				t.Fatal("API canonical mask/liveness differs", title, employment, sqlReserved, active, failures)
			}
			score, err := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
			if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("API recurring deadline/lease differs", err)
			}
			if status == "succeeded" {
				var html, currency string
				var uploaded bool
				var hash *int64
				var pending int64
				if err := f.pg.QueryRow(ctx, `SELECT d.html,p.salary_currency,d.r2_uploaded,p.description_r2_hash,d.hash FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid`, f.original).Scan(&html, &currency, &uploaded, &hash, &pending); err != nil || !strings.Contains(html, "Build reliable services.") || currency != "CHF" || uploaded || hash != nil || pending == 0 {
					t.Fatal("API canonical content staging differs", err)
				}
			}
		})
	}
}

func TestHTTPAPIDetailFullJobContentMatchesFrozenPython(t *testing.T) {
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_http_detail.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Source string
		Config       map[string]any
		Payload      json.RawMessage
		AuthPayload  json.RawMessage `json:"auth_payload"`
		Expected     map[string]any
	}
	if json.Unmarshal(raw, &cases) != nil {
		t.Fatal("corpus decode")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, err := apisniffer.HTTPDetailOptionsForSource(c.Config, c.Source)
			if err != nil {
				t.Fatal(err)
			}
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if r.Host == "auth.example.net" {
					w.Write(c.AuthPayload)
				} else {
					w.Write(c.Payload)
				}
			})
			p := queue.WorkdayDetailProfile{Profile: "api_sniffer.http-detail/v1", HTTPAPIConfig: c.Config, SourceURL: c.Source, Endpoint: o.Request.URL}
			got, reserved, err := fetchAPIDetail(context.Background(), client, p)
			if err != nil || reserved != nil || !reflect.DeepEqual(got, c.Expected) {
				t.Fatal("HTTP JobContent reference differs", got, c.Expected, err)
			}
		})
	}
}
