package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestGroupedDetailTransportOriginalPublicFieldsAndRequests(t *testing.T) {
	dir := os.Getenv("JOBSEEK_DETAIL_TRANSPORT_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original detail transport captures")
	}
	var input struct {
		Records []struct {
			ID, Kind          string
			Canonical         map[string]string
			ScheduledPostings int `json:"scheduled_postings"`
		}
	}
	raw, err := os.ReadFile(filepath.Join(dir, "native1012-detail-transport-group-frozen1010-input1-2026-10-11.json"))
	if err != nil || json.Unmarshal(raw, &input) != nil || len(input.Records) != 37 {
		t.Fatal("complete source-bound group unavailable")
	}
	for _, row := range input.Records {
		t.Run(row.Canonical["board_slug"], func(t *testing.T) {
			var capture struct {
				Samples []struct {
					SourceURL string `json:"source_url"`
					Status    string
					Content   map[string]any
					Exchanges []struct {
						Method, URL string
						Status      int
						Body        string            `json:"body_base64"`
						Headers     map[string]string `json:"response_headers"`
					}
				}
			}
			suffix := "capture1"
			if row.Kind == "dom-tls" {
				suffix = "capture2"
			}
			filename := fmt.Sprintf("native1012-detail-transport-%s-original-public-%s-2026-10-11.json", row.Canonical["board_slug"], suffix)
			raw, err := os.ReadFile(filepath.Join(dir, filename))
			if err != nil || json.Unmarshal(raw, &capture) != nil {
				t.Fatal("complete original samples unavailable")
			}
			if len(capture.Samples) == 0 {
				if row.ScheduledPostings != 0 {
					t.Fatal("scheduled routes lack original samples")
				}
				t.Log("held: no actual scheduled route; no public detail qualification")
				return
			}
			for i, sample := range capture.Samples {
				t.Run(fmt.Sprint(i), func(t *testing.T) {
					if sample.Status != "completed-original-detail" || len(sample.Exchanges) == 0 {
						t.Fatal("original did not complete")
					}
					inspect := queue.InspectWorkdayDetail
					if row.Kind == "dom-tls" {
						inspect = queue.InspectDOMDetail
					} else if row.Kind == "jsonld-tls" {
						inspect = queue.InspectJSONLDDetail
					}
					profile, err := inspect(row.ID, row.Canonical, sample.SourceURL, queue.Simple)
					if err != nil || profile.SkipSSL != (row.Kind != "workday-proxy") || queue.ProfileRequiresProxy(profile.Profile) != (row.Kind == "workday-proxy") {
						t.Fatal("canonical transport was not admitted", err)
					}
					calls := 0
					client := &VerifiedHTTP{client: verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						if calls >= len(sample.Exchanges) {
							t.Error("extra request")
							w.WriteHeader(400)
							return
						}
						original := sample.Exchanges[calls]
						calls++
						parsed, err := url.Parse(original.URL)
						if err != nil || original.Method != r.Method || parsed.Host != r.Host || parsed.EscapedPath() != r.URL.EscapedPath() || !reflect.DeepEqual(parsed.Query(), r.URL.Query()) {
							t.Error("original request method, host, path or query changed")
						}
						for key, value := range original.Headers {
							w.Header().Set(key, value)
						}
						body, err := base64.StdEncoding.DecodeString(original.Body)
						if err != nil {
							t.Fatal("captured body unavailable")
						}
						w.WriteHeader(original.Status)
						_, _ = w.Write(body)
					})), proxyRequired: row.Kind == "workday-proxy", skipSSL: profile.SkipSSL}
					var content any
					if row.Kind == "workday-proxy" {
						result, err := FetchWorkdayDetail(context.Background(), client, sample.SourceURL, profile.FacilityTenantAliases)
						if err != nil {
							t.Fatal("original Workday detail failed", err)
						}
						content = result.Content
					} else {
						result, err := fetchDirectDetail(context.Background(), client, profile)
						if err != nil {
							t.Fatal("original document detail failed", err)
						}
						content = result.Content
					}
					body, err := json.Marshal(content)
					var actual map[string]any
					if err != nil || json.Unmarshal(body, &actual) != nil {
						t.Fatal("native projection unavailable")
					}
					for _, field := range []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "extras", "metadata"} {
						if !reflect.DeepEqual(actual[field], sample.Content[field]) {
							t.Errorf("original detail field changed: %s", field)
						}
					}
					if calls != len(sample.Exchanges) {
						t.Fatal("original request sequence incomplete")
					}
				})
			}
		})
	}
}
