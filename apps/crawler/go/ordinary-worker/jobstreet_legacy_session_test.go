package worker

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestJobStreetOriginalSalaryAndDetailsThroughHTTP(t *testing.T) {
	var cases []struct {
		Name, Kind, Host string
		Page             json.RawMessage
		Expected         json.RawMessage
		Error            bool
	}
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_jobstreet.json")
	if e != nil {
		t.Fatal(e)
	}
	if json.Unmarshal(raw, &cases) != nil {
		t.Fatal("original corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			var job api.JobStreetJob
			var e error
			if c.Kind == "page" {
				var rows []api.JobStreetJob
				rows, _, e = api.ParseJobStreetPage(c.Page, api.JobStreetOptions{Host: c.Host, CompanyID: "175608148114568", OrganisationID: "744981"}, 1)
				if !c.Error && len(rows) == 0 {
					return
				}
				if len(rows) > 0 {
					job = rows[0]
				}
			} else {
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method != "POST" || r.Host != c.Host || r.URL.Path != "/graphql" {
						t.Error("GraphQL resource")
					}
					body, _ := io.ReadAll(r.Body)
					request, _, _, _ := api.JobStreetDetailRequest("https://"+c.Host+"/job/12345678", nil)
					var got, want any
					_ = json.Unmarshal(body, &got)
					_ = json.Unmarshal([]byte(request.Body), &want)
					if !reflect.DeepEqual(got, want) {
						t.Error("original query/locale drift")
					}
					w.Header().Set("Content-Type", "application/json")
					_, _ = w.Write(c.Page)
				}))
				fields, reserved, err := fetchJobStreetDetail(context.Background(), client.client, queue.WorkdayDetailProfile{SourceURL: "https://" + c.Host + "/job/12345678", Endpoint: "https://" + c.Host + "/graphql", Profile: "jobstreet.graphql-detail/v1"}, func(context.Context, time.Duration) error { return nil })
				if reserved != nil {
					t.Fatal("unexpected reservation")
				}
				if errors.Is(err, executor.ErrEmptyResult) {
					err = nil
					fields = map[string]any{}
				}
				e = err
				job.Fields = fields
			}
			if (e != nil) != c.Error {
				t.Fatalf("error%v expected refusal%v", e, c.Error)
			}
			if c.Error {
				return
			}
			fields, e := jobStreetSalary(job, c.Host)
			if e != nil {
				t.Fatal(e)
			}
			var want any
			_ = json.Unmarshal(c.Expected, &want)
			if c.Kind == "page" {
				want = want.([]any)[0]
			}
			body, _ := json.Marshal(fields)
			var got any
			_ = json.Unmarshal(body, &got)
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("original fields/salary differ\nactual%s\nexpected%s", body, c.Expected)
			}
		})
	}
}
func TestSuccessFactorsLegacyHTTPOriginalSessionAndPrefixes(t *testing.T) {
	var corpus struct {
		Inventories []struct {
			Name  string
			Board struct {
				BoardURL string `json:"board_url"`
				Metadata json.RawMessage
			}
			Responses []struct {
				Body    string
				Headers map[string]string
			}
			Requests         []struct{ Method, URL, Body string }
			Expected         json.RawMessage
			Truncated, Error bool
		}
	}
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_successfactors_legacy.json")
	if e != nil {
		t.Fatal(e)
	}
	if json.Unmarshal(raw, &corpus) != nil {
		t.Fatal("original corpus")
	}
	nonce := regexp.MustCompile(`(?m)^scriptSessionId=([0-9a-f]{24})$`)
	for _, c := range corpus.Inventories {
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"board_url": c.Board.BoardURL, "metadata": string(c.Board.Metadata)}
			o, e := api.SuccessFactorsLegacyOptionsFromMetadata(config["board_url"], config["metadata"])
			if e != nil {
				t.Fatal(e)
			}
			p := queue.GreenhouseMonitorProfile{Provider: "rss", Profile: "rss.successfactors-legacy-session-items/v1", Endpoint: o.ListingURL(), Token: o.Company}
			calls := 0
			session := ""
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				i := calls
				calls++
				if i >= len(c.Responses) || i >= len(c.Requests) {
					t.Error("unexpected request")
					w.WriteHeader(500)
					return
				}
				want := c.Requests[i]
				body, _ := io.ReadAll(r.Body)
				if r.Method == "POST" {
					cookie, e := r.Cookie("JSESSIONID")
					if e != nil || cookie.Value != "fixture-session" {
						t.Error("bootstrap cookie lost")
					}
					match := nonce.FindStringSubmatch(string(body))
					if len(match) != 2 {
						t.Error("session nonce")
					}
					if session == "" && len(match) == 2 {
						session = match[1]
					}
					if len(match) == 2 && session != match[1] {
						t.Error("session nonce changed")
					}
					body = []byte(nonce.ReplaceAllString(string(body), "scriptSessionId=0123456789abcdef01234567"))
				}
				if r.Method != want.Method || "https://"+r.Host+r.URL.RequestURI() != want.URL || string(body) != want.Body {
					t.Error("original session request drift")
				}
				for k, v := range c.Responses[i].Headers {
					// The frozen mock length predates the malformed bootstrap mutation.
					// Let the real server frame its actual bytes.
					if !strings.EqualFold(k, "Content-Length") {
						w.Header().Set(k, v)
					}
				}
				if i == 0 {
					w.Header().Set("Set-Cookie", "JSESSIONID=fixture-session; Path=/; Secure")
				}
				_, _ = w.Write([]byte(c.Responses[i].Body))
			}))
			out, e := FetchSuccessFactorsLegacyHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
			if (e != nil) != c.Error || calls != len(c.Requests) {
				t.Fatal(e, calls, len(c.Requests))
			}
			if c.Error {
				if strings.Contains(c.Name, "later") || c.Name == "changed-total" {
					var prefix *rssStreamPrefixError
					if !errors.As(e, &prefix) || len(out.Jobs) != 100 {
						t.Fatal("validated original100-job prefix lost", e, len(out.Jobs))
					}
				}
				return
			}
			var expected []map[string]any
			_ = json.Unmarshal(c.Expected, &expected)
			if len(out.Jobs) != len(expected) || out.Truncated != c.Truncated {
				t.Fatal("inventory outcome")
			}
			for i, field := range expected {
				locations, ok := field["locations"].([]any)
				if ok {
					converted := []string{}
					for _, v := range locations {
						converted = append(converted, v.(string))
					}
					field["locations"] = converted
				}
				want, e := secondaryRichJob(field)
				if e != nil {
					t.Fatal(e)
				}
				want.Hybrid = true
				if !reflect.DeepEqual(jsonNormalizedRichJob(t, out.Jobs[i]), jsonNormalizedRichJob(t, want)) {
					t.Fatal("original retained fields differ")
				}
			}
		})
	}
}
func TestJobStreetLegacyReservationBeforeMalformedBody(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		t.Run(map[bool]string{false: "jobstreet", true: "legacy"}[legacy], func(t *testing.T) {
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("TDM-Reservation", "1")
				_, _ = w.Write([]byte("malformed"))
			}))
			var scope providerResourceScope
			var request api.Request
			if legacy {
				o := api.SuccessFactorsLegacyOptions{Host: "career5.successfactors.eu", Company: "Acme"}
				scope = o
				request = api.Request{Method: "GET", URL: o.ListingURL()}
			} else {
				o := api.JobStreetOptions{Host: "sg.jobstreet.com", CompanyID: "175608148114568", OrganisationID: "744981", Locale: "en-SG", SiteKey: "sg"}
				scope = o
				request = o.PageRequest(1)
			}
			_, _, observed, e := fetchJobStreetLegacyResource(context.Background(), client.client, scope, request, legacy, func(context.Context, time.Duration) error { t.Fatal("reserved response retried"); return nil })
			var reserved *policy.Reservation
			if !errors.As(e, &reserved) || observed == nil || !observed.reserved {
				t.Fatal("publisher evidence lost", e)
			}
		})
	}
}
