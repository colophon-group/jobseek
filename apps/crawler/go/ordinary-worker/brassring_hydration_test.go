package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"

	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestBrassRingHydrationCannotPublishIncompleteOrForeignDetails(t *testing.T) {
	board := "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=5998"
	detail := "https://sjobs.brassring.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25416&siteid=5998&PageType=JobDetails&jobid="
	for _, mode := range []string{"complete", "missing", "wrong-id", "reserved", "foreign-redirect", "wrong-tenant", "identity-mismatch", "canceled"} {
		t.Run(mode, func(t *testing.T) {
			var requests atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests.Add(1)
				if r.Host != "sjobs.brassring.com" || r.Method != "GET" || strings.Contains(r.Header.Get("Cookie"), "foreign_cookie") {
					t.Error("public detail scope or operation cookies changed")
				}
				id := r.URL.Query().Get("jobid")
				if id == "2" {
					switch mode {
					case "reserved":
						w.Header().Set("TDM-Reservation", "1")
						fmt.Fprint(w, "unparseable reserved body")
						return
					case "foreign-redirect":
						http.Redirect(w, r, "https://evil.test/jobs", 302)
						return
					case "wrong-id":
						id = "3"
					}
				}
				questions := []map[string]string{{"VerityZone": "formtext8", "AnswerValue": "Boston"}}
				if mode == "missing" && id == "2" {
					questions = nil
				}
				body, _ := json.Marshal(map[string]any{"JobId": id, "Jobdetails": map[string]any{"JobDetailQuestions": questions}})
				fmt.Fprintf(w, `<input id="preLoadJSON" value="%s">`, html.EscapeString(string(body)))
			}))
			client.client.Jar, _ = cookiejar.New(nil)
			base, _ := url.Parse(board)
			client.client.Jar.SetCookies(base, []*http.Cookie{{Name: "foreign_cookie", Value: "fixture", Path: "/"}})
			snapshot := RichDiscovery{Jobs: []RichMonitorJob{{URL: detail + "1", Metadata: map[string]any{"requisition_id": "1"}}, {URL: detail + "2", Metadata: map[string]any{"requisition_id": "2"}}, {URL: detail + "3", Metadata: map[string]any{"requisition_id": "3"}, Locations: []string{"London"}}}}
			if mode == "wrong-tenant" {
				snapshot.Jobs[1].URL = strings.Replace(snapshot.Jobs[1].URL, "siteid=5998", "siteid=9", 1)
			}
			if mode == "identity-mismatch" {
				snapshot.Jobs[1].Metadata["requisition_id"] = "3"
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if mode == "canceled" {
				cancel()
			}
			got, err := hydrateBrassRingSnapshot(ctx, client.client, board, snapshot)
			if mode == "complete" {
				if err != nil || requests.Load() != 2 || len(got.Jobs) != 3 || len(got.Jobs[0].Locations) != 1 || got.Jobs[0].Locations[0] != "Boston" || got.Jobs[2].Locations[0] != "London" {
					t.Fatal("complete public hydration changed", err)
				}
			} else if err == nil || len(got.Jobs) != 0 {
				t.Fatal("incomplete snapshot became authoritative", err)
			}
			if mode == "reserved" {
				var reserved *policy.Reservation
				if !errors.As(err, &reserved) || got.Response == nil || !got.Response.reserved || got.Response.finalURL != reserved.URL {
					t.Fatal("publisher reservation lost", err)
				}
			}
			if len(snapshot.Jobs[0].Locations) != 0 || len(snapshot.Jobs[1].Locations) != 0 {
				t.Fatal("caller retained provisional hydration")
			}
			if (mode == "wrong-tenant" || mode == "identity-mismatch" || mode == "canceled") && requests.Load() != 0 {
				t.Fatal("invalid snapshot made a public request")
			}
		})
	}
}
