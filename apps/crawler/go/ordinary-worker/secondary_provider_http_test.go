package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func noSecondaryWait(context.Context, time.Duration) error { return nil }
func TestSecondaryProvidersHTTPInventories(t *testing.T) {
	cases := []struct{ provider, profile, board, endpoint, metadata, body string }{
		{"softgarden", "softgarden.inline-urls/v1", "https://acme.softgarden.io", "https://acme.softgarden.io", `{}`, `var complete_job_id_list=[123,456];`},
		{"ukg", "ukg.search-items/v1", "https://recruiting.ultipro.com/ABC123/JobBoard/11111111-1111-1111-1111-111111111111", "https://recruiting.ultipro.com/ABC123/JobBoard/11111111-1111-1111-1111-111111111111/JobBoardView/LoadSearchResults", `{}`, `{"totalCount":1,"opportunities":[{"Id":"22222222-2222-2222-2222-222222222222","Title":"Engineer","BriefDescription":"<p>Build</p>","FullTime":true}]}`},
		{"bamboohr", "bamboohr.careers-list/v1", "https://acme.bamboohr.com/careers", "https://acme.bamboohr.com/careers/list", `{}`, `{"meta":{"totalCount":1},"result":[{"id":123,"jobOpeningName":"Engineer"}]}`},
		{"recruiter_co_kr", "recruiter-co-kr.jobflex/v1", "https://acme.recruiter.co.kr/career/home", "https://api-recruiter.recruiter.co.kr/position/v1/jobflex", `{}`, `{"pagination":{"totalPages":1},"list":[{"positionSn":123,"title":"Engineer"}]}`},
	}
	for _, c := range cases {
		for _, mode := range []string{"complete", "reserved", "failure", "transient"} {
			t.Run(c.provider+"/"+mode, func(t *testing.T) {
				var lists, details atomic.Int32
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if strings.Contains(r.URL.Path, "/position/v2/") {
						details.Add(1)
						if r.Header.Get("Prefix") != "acme.recruiter.co.kr" {
							t.Error("missing tenant header")
						}
						fmt.Fprint(w, `{"title":"Engineer","jobDescription":"<p>Build</p>","startDateTime":"2026-10-06T00:00:00"}`)
						return
					}
					n := lists.Add(1)
					if c.provider == "ukg" || c.provider == "recruiter_co_kr" {
						if r.Method != "POST" {
							t.Error("missing POST")
						}
						body, _ := io.ReadAll(r.Body)
						var raw map[string]any
						if json.Unmarshal(body, &raw) != nil {
							t.Error("bad payload")
						}
					}
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
					}
					if mode == "failure" || mode == "transient" && n == 1 {
						w.WriteHeader(503)
						return
					}
					fmt.Fprint(w, c.body)
				}))
				config := map[string]string{"crawler_type": c.provider, "monitor_needs_browser": "0", "board_url": c.board, "metadata": c.metadata}
				p := queue.GreenhouseMonitorProfile{Provider: c.provider, Profile: c.profile, Endpoint: c.endpoint}
				var got RichDiscovery
				var err error
				switch c.provider {
				case "softgarden":
					got, err = discoverSoftgardenInventory(context.Background(), client.client, p, config)
				case "ukg":
					got, err = discoverUKGInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
				case "bamboohr":
					got, err = discoverBambooHRInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
				default:
					got, err = discoverRecruiterKRInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
				}
				if mode == "reserved" {
					var reservation *policy.Reservation
					if !errors.As(err, &reservation) || len(got.Jobs) != 0 {
						t.Fatalf("reservation lost: %v", err)
					}
					return
				}
				if mode == "failure" || mode == "transient" && c.provider == "softgarden" {
					if err == nil || len(got.Jobs) != 0 {
						t.Fatal("failed inventory became complete")
					}
					return
				}
				if err != nil || got.Truncated {
					t.Fatalf("complete inventory failed: %v", err)
				}
				want := 1
				if c.provider == "softgarden" {
					want = 2
				}
				if len(got.Jobs) != want {
					t.Fatalf("jobs %d expected %d", len(got.Jobs), want)
				}
				if c.provider == "softgarden" && !got.Jobs[0].URLOnly {
					t.Fatal("URL-only listing became rich")
				}
				if c.provider == "recruiter_co_kr" && (details.Load() != 1 || got.Jobs[0].Description == nil || got.Jobs[0].DatePosted != "2026-10-05") {
					t.Fatal("hydration or KST date changed")
				}
				if mode == "transient" && lists.Load() != 2 {
					t.Fatal("retry budget changed")
				}
			})
		}
	}
}

func TestSecondaryRequiredDetailReservationOutranksSiblingFailure(t *testing.T) {
	for _, provider := range []string{"bamboohr", "recruiter_co_kr"} {
		t.Run(provider, func(t *testing.T) {
			board, endpoint, profile, metadata := "https://acme.bamboohr.com/careers", "https://acme.bamboohr.com/careers/list", "bamboohr.careers-list/v1", `{"description_include_regex":"Build"}`
			if provider == "recruiter_co_kr" {
				board, endpoint, profile, metadata = "https://acme.recruiter.co.kr/career/home", "https://api-recruiter.recruiter.co.kr/position/v1/jobflex", "recruiter-co-kr.jobflex/v1", `{}`
			}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasSuffix(r.URL.Path, "/123/detail") || strings.HasSuffix(r.URL.Path, "/123") {
					w.WriteHeader(503)
					return
				}
				if strings.HasSuffix(r.URL.Path, "/456/detail") || strings.HasSuffix(r.URL.Path, "/456") {
					w.Header().Set("TDM-Reservation", "1")
					fmt.Fprint(w, `{}`)
					return
				}
				if provider == "bamboohr" {
					fmt.Fprint(w, `{"meta":{"totalCount":2},"result":[{"id":123,"jobOpeningName":"Engineer"},{"id":456,"jobOpeningName":"Designer"}]}`)
				} else {
					fmt.Fprint(w, `{"pagination":{"totalPages":1},"list":[{"positionSn":123,"title":"Engineer"},{"positionSn":456,"title":"Designer"}]}`)
				}
			}))
			config := map[string]string{"crawler_type": provider, "monitor_needs_browser": "0", "board_url": board, "metadata": metadata}
			p := queue.GreenhouseMonitorProfile{Provider: provider, Profile: profile, Endpoint: endpoint}
			var got RichDiscovery
			var err error
			if provider == "bamboohr" {
				got, err = discoverBambooHRInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
			} else {
				got, err = discoverRecruiterKRInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
			}
			var reserved *policy.Reservation
			if !errors.As(err, &reserved) || got.Response == nil || !got.Response.reserved || !strings.Contains(got.Response.endpoint, "456") || len(got.Jobs) != 0 {
				t.Fatal("sibling failure erased resource reservation", err)
			}
		})
	}
}
