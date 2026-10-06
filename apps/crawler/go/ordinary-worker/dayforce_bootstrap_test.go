package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestDayforceBootstrapMatchesActualPythonHTTP(t *testing.T) {
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_dayforce_bootstrap.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus []struct {
		Name  string
		Pages map[string]struct {
			Status         int
			Body, Location string
		}
		Requests []string
		Expected struct {
			Error, Gone bool
			Site        map[string]any
		}
	}
	if json.Unmarshal(raw, &corpus) != nil || len(corpus) != 7 {
		t.Fatal("actual Python bootstrap corpus unavailable")
	}
	board := api.DayforceBoard{Tenant: "fixture", Portal: "Careers"}
	for _, c := range corpus {
		t.Run(c.Name, func(t *testing.T) {
			requests := []string{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				source := "https://" + r.Host + r.URL.String()
				requests = append(requests, source)
				p, ok := c.Pages[source]
				if !ok || r.Method != "GET" {
					t.Error("request left frozen source", source)
					w.WriteHeader(500)
					return
				}
				if p.Location != "" {
					w.Header().Set("Location", p.Location)
				}
				w.WriteHeader(p.Status)
				fmt.Fprint(w, p.Body)
			}))
			_, site, _, e := bootstrapDayforce(context.Background(), client.client, board, noSecondaryWait)
			if !reflect.DeepEqual(requests, c.Requests) {
				t.Fatalf("bootstrap requests=%v expected=%v", requests, c.Requests)
			}
			if c.Expected.Gone {
				var gone *DiscoveryError
				if !errors.As(e, &gone) || gone.Kind != "provider_gone" {
					t.Fatal("gone classification lost", e)
				}
				return
			}
			if c.Expected.Error {
				if e == nil {
					t.Fatal("invalid bootstrap became content")
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			body, _ := json.Marshal(map[string]any{"job_board_id": site.JobBoardID, "culture": site.Culture, "cultures": site.Cultures, "disabled": site.Disabled})
			var got map[string]any
			json.Unmarshal(body, &got)
			if !reflect.DeepEqual(got, c.Expected.Site) {
				t.Fatalf("site=%s expected=%v", body, c.Expected.Site)
			}
		})
	}
}
