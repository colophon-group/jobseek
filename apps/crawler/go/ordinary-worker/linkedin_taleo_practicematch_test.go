package worker

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestLinkedInTaleoPracticeMatchHTTPInventoriesRetryPublisherAndGone(t *testing.T) {
	var linkedin struct {
		Inventories []struct {
			Name      string
			Responses []*string
		}
	}
	var taleo struct {
		Inventories []struct {
			Name   string
			Bodies map[string]*string
		}
	}
	for file, target := range map[string]any{"python_linkedin.json": &linkedin, "python_taleo.json": &taleo} {
		raw, err := os.ReadFile("../api-sniffer-monitor/testdata/" + file)
		if err != nil || json.Unmarshal(raw, target) != nil {
			t.Fatal(err)
		}
	}
	liBodies := []*string{}
	for _, c := range linkedin.Inventories {
		if c.Name == "pagination" {
			liBodies = c.Responses
		}
	}
	tBodies := map[string]*string{}
	for _, c := range taleo.Inventories {
		if c.Name == "total-complete" {
			tBodies = c.Bodies
		}
	}
	if len(liBodies) != 2 || len(tBodies) != 2 {
		t.Fatal("full Python fixtures absent")
	}
	for _, provider := range []string{"linkedin", "taleo", "practicematch"} {
		c := map[string]string{"crawler_type": provider, "monitor_needs_browser": "0"}
		p := queue.GreenhouseMonitorProfile{Provider: provider}
		switch provider {
		case "linkedin":
			c["board_url"], c["metadata"] = "https://www.linkedin.com/company/acme/jobs/", `{"company_id":"42","company_slug":"acme"}`
			p.Profile, p.Endpoint = "linkedin.guest-items/v1", api.LinkedInListingRequest("42", "", 0).URL
		case "taleo":
			c["board_url"], c["metadata"] = "https://phe.tbe.taleo.net/phe01/ats/careers/v2/searchResults?org=ACME&cws=1", `{"host":"phe.tbe.taleo.net","partition":"phe01","org":"ACME","cws":1}`
			p.Profile, p.Endpoint = "taleo.listing-urls/v1", c["board_url"]
		case "practicematch":
			c["board_url"], c["metadata"] = "https://employer.practicematch.com/employer/fixture/", `{"proxy":true}`
			p.Profile, p.Endpoint = "practicematch.proxy-listing-urls/v1", c["board_url"]
		}
		for _, mode := range []string{"complete", "retry", "reserved503", "reserved404", "late-reserved", "body-reserved", "incomplete-reserved", "incomplete", "foreign-redirect", "late-failure", "gone", "gone-child", "status202"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				calls, waits := 0, 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					child := provider == "linkedin" && r.URL.Query().Get("start") == "10" || provider == "taleo" && r.URL.Query().Get("rowFrom") == "10" || provider == "practicematch" && r.Method == "POST"
					if r.Header.Get("Authorization") != "" || r.Header.Get("Proxy-Authorization") != "" {
						t.Error("upstream credential header")
					}
					if provider == "linkedin" && r.Header.Get("Accept-Language") != "en-US,en;q=0.9" {
						t.Error("language scope")
					}
					if strings.HasPrefix(mode, "reserved") || mode == "late-reserved" && child {
						w.Header().Set("TDM-Reservation", "1")
						if mode == "reserved404" {
							w.WriteHeader(404)
						} else {
							w.WriteHeader(503)
						}
						return
					}
					if mode == "body-reserved" || mode == "incomplete-reserved" {
						w.Header().Set("Content-Type", "text/html")
						if mode == "incomplete-reserved" {
							w.Header().Set("Content-Length", "1000")
						}
						w.WriteHeader(503)
						w.Write([]byte(`<meta name="tdm-reservation" content="1">`))
						return
					}
					if mode == "incomplete" {
						w.Header().Set("Content-Length", "1000")
						w.Write([]byte("broken"))
						return
					}
					if mode == "foreign-redirect" {
						w.Header().Set("Location", "https://foreign.example/jobs")
						w.WriteHeader(302)
						return
					}
					if mode == "retry" && calls == 1 {
						w.WriteHeader(503)
						return
					}
					if mode == "gone" || mode == "gone-child" && child {
						w.WriteHeader(404)
						return
					}
					if mode == "late-failure" && child {
						w.Write([]byte("not a valid inventory"))
						return
					}
					if mode == "status202" {
						w.WriteHeader(202)
						w.Write([]byte("not complete"))
						return
					}
					w.Header().Set("Content-Type", "text/html; charset=UTF-8")
					switch provider {
					case "linkedin":
						index := 0
						if child {
							index = 1
						}
						w.Write([]byte(*liBodies[index]))
					case "taleo":
						key := "0"
						if child {
							key = "10"
						}
						w.Write([]byte(*tBodies[key]))
					case "practicematch":
						link := func(id string) string {
							return `<a href="https://www.practicematch.com/physicians/job-details.cfm/` + id + `/role?tracking=1">Job</a>`
						}
						if r.Method == "GET" {
							w.Write([]byte(`<input id="facilityID" value="42"><input id="facilityLandingURL" value="A &amp; B">` + link("1")))
							return
						}
						if r.Header.Get("Referer") != c["board_url"] || r.Header.Get("X-Requested-With") != "XMLHttpRequest" || r.Header.Get("Content-Type") != "application/x-www-form-urlencoded; charset=UTF-8" {
							t.Error("POST request headers")
						}
						raw, _ := io.ReadAll(r.Body)
						form, err := url.ParseQuery(string(raw))
						if err != nil || form.Get("facilityID") != "42" {
							t.Error("form scope", string(raw))
						}
						html := ""
						if form.Get("professionID") == "1" && form.Get("pageNum") == "2" {
							html = link("2")
						}
						if form.Get("professionID") == "-1" && form.Get("pageNum") == "1" {
							html = link("3")
						}
						w.Header().Set("Content-Type", "application/json")
						json.NewEncoder(w).Encode(map[string]string{"OPPLISTINGSHTML": html})
					}
				}))
				out, err := FetchLinkedInTaleoPracticeMatchHTTP(context.Background(), client.client, p, c, func(context.Context, time.Duration) error { waits++; return nil })
				success := mode == "complete" || mode == "retry" || provider == "linkedin" && (mode == "gone" || mode == "gone-child")
				if (err == nil) != success || !success && len(out.Jobs) > 0 {
					t.Fatal("outcome differs", mode, len(out.Jobs), err)
				}
				if success {
					want := map[string]int{"linkedin": 11, "taleo": 17, "practicematch": 3}[provider]
					if mode == "gone" {
						want = 0
					}
					if mode == "gone-child" {
						want = 10
					}
					if len(out.Jobs) != want {
						t.Fatal("partial inventory", len(out.Jobs), want)
					}
					if provider == "linkedin" && len(out.Jobs) > 0 && (out.Jobs[0].Title == nil || len(out.Jobs[0].Locations) != 1 || out.Jobs[0].DatePosted == nil || out.Jobs[0].Metadata["job_id"] == nil) {
						t.Fatal("rich fields lost", out.Jobs[0])
					}
				}
				if strings.Contains(mode, "reserved") {
					var reservation *policy.Reservation
					if !errors.As(err, &reservation) || out.Response == nil || !out.Response.reserved {
						t.Fatal("reservation lost", err, out.Response)
					}
					if mode != "late-reserved" && calls != 1 {
						t.Fatal("reserved response retried", calls)
					}
				}
				if mode == "gone" && provider == "taleo" {
					var failure *DiscoveryError
					if !errors.As(err, &failure) || failure.Kind != "provider_gone" || !queue.SecondaryMonitorGone(c, out.Response.endpoint, out.Response.status, false) {
						t.Fatal("primary tombstone lost", err)
					}
				}
				if mode == "gone-child" && provider == "taleo" && queue.SecondaryMonitorGone(c, out.Response.endpoint, out.Response.status, false) {
					t.Fatal("child failure delisted board")
				}
				if mode == "retry" && waits == 0 {
					t.Fatal("bounded retry missing")
				}
			})
		}
	}
}

func TestTaleoDispatcherIdentityAndChallengeProtection(t *testing.T) {
	b := api.TaleoBoard{Host: "phe.tbe.taleo.net", Partition: "phe01", Org: "ACME", CWS: 1}
	p := queue.GreenhouseMonitorProfile{Provider: "taleo", Profile: "taleo.listing-urls/v1", Endpoint: b.ListingURL(nil)}
	c := map[string]string{"crawler_type": "taleo", "board_url": p.Endpoint, "monitor_needs_browser": "0", "metadata": `{"host":"phe.tbe.taleo.net","partition":"phe01","org":"ACME","cws":1}`}
	dispatcher := "https://phe.tbe.taleo.net/dispatcher/servlet/DispatcherServlet?org=ACME&act=redirectCws&redirectUrl=" + url.QueryEscape(p.Endpoint)
	for _, mode := range []string{"inactive", "dispatcher404", "configured-migration", "challenge"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if mode == "challenge" {
					w.Write([]byte(`<title>Just a moment</title><section class="oracletaleocwsv2-search-results"></section>`))
					return
				}
				if mode == "configured-migration" {
					w.Header().Set("Location", "https://phh.tbe.taleo.net/phh01/ats/careers/v2/searchResults?org=ACME&cws=41")
					w.WriteHeader(302)
					return
				}
				if calls == 1 {
					w.Header().Set("Location", dispatcher)
					w.WriteHeader(302)
					return
				}
				if mode == "dispatcher404" {
					w.WriteHeader(404)
					return
				}
				w.Header().Set("Location", "INACTIVEcareers/v2/searchResults?org=ACME&cws=1")
				w.WriteHeader(302)
			}))
			out, err := FetchLinkedInTaleoPracticeMatchHTTP(context.Background(), client.client, p, c, func(context.Context, time.Duration) error { return nil })
			if err == nil || len(out.Jobs) > 0 {
				t.Fatal("unproved inventory became success", out, err)
			}
			if mode == "inactive" || mode == "dispatcher404" {
				var failure *DiscoveryError
				if !errors.As(err, &failure) || failure.Kind != "provider_gone" || !queue.SecondaryMonitorGone(c, out.Response.endpoint, out.Response.status, out.Response.providerDisabled) {
					t.Fatal("exact primary tombstone lost", out.Response, err)
				}
			}
			if mode == "configured-migration" && calls != 1 || mode == "challenge" && calls != 1 {
				t.Fatal("unproved first page retried", calls)
			}
		})
	}
}
