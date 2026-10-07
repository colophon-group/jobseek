package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type providerBatchDetailCase struct {
	Name, URL string
	Config    map[string]any
	Pages     map[string]json.RawMessage
	Requests  []struct {
		Method, URL string
		Body        any
	}
	Expected       map[string]any
	NativeReserved bool `json:"native_reserved"`
	NativeEmpty    bool `json:"native_empty"`
}

func providerBatchDetailReference(t *testing.T, provider, name string) providerBatchDetailCase {
	t.Helper()
	suffix := provider
	if provider == "mokahr" {
		suffix = "mokahr_detail"
	}
	raw, err := os.ReadFile("testdata/python_" + suffix + ".json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct{ HTTP []providerBatchDetailCase }
	if err := json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	for _, c := range corpus.HTTP {
		if c.Name == name {
			return c
		}
	}
	t.Fatal("missing actual detail reference", provider, name)
	return providerBatchDetailCase{}
}

func TestRealProviderBatchDetailReferenceHTTPCommitsCanonicalContentAndSettlement(t *testing.T) {
	realProviderBatchDetailTransportCases(t, false)
}
func TestRealProxyEightfoldDetailReferenceHTTPCommitsCanonicalContentAndSettlement(t *testing.T) {
	realProviderBatchDetailTransportCases(t, true)
}
func realProviderBatchDetailTransportCases(t *testing.T, proxy bool) {
	cases := map[string][]string{
		"mokahr":    {"complete", "description-mask", "empty-backfill", "custom-locale", "campus-fallback", "bootstrap-redirect", "bootstrap-redirect-reserved", "detail-redirect-refused", "missing-bootstrap", "detail-status-500", "detail-bad-json", "bootstrap-reserved", "detail-reserved", "foreign-id"},
		"eightfold": {"jsonld-fastpath", "description-mask", "empty-backfill", "api-fallback", "html-gone-api-recovers", "html-redirect", "html-redirect-reserved", "api-redirect-refused", "missing-title-merge", "no-content", "api-bad-json", "api-201", "html-reserved", "api-reserved"},
	}
	for _, provider := range []string{"mokahr", "eightfold"} {
		if proxy && provider != "eightfold" {
			continue
		}
		for _, mode := range cases[provider] {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				reference := mode
				if mode == "description-mask" || mode == "empty-backfill" {
					reference = "complete"
					if provider == "eightfold" {
						reference = "jsonld-fastpath"
					}
				}
				c := providerBatchDetailReference(t, provider, reference)
				config := c.Config
				if config == nil {
					config = map[string]any{}
				}
				if mode == "description-mask" || mode == "empty-backfill" {
					config["enrich"] = []string{"description"}
				}
				if proxy {
					config["proxy"] = true
				}
				metadata, err := json.Marshal(map[string]any{"scraper_type": provider, "scraper_config": config})
				if err != nil {
					t.Fatal(err)
				}
				f, a, claim := independentDetailOwnedFixture(t, string(metadata), c.URL)
				ctx := context.Background()
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Monitor title'],employment_type='part_time',location_ids=ARRAY[2] WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
				if mode == "empty-backfill" {
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY[]::text[],employment_type=NULL,location_ids=ARRAY[]::integer[] WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
				}
				calls := []struct {
					Method, URL string
					Body        any
				}{}
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					source := "https://" + r.Host + r.URL.String()
					var body any
					if r.Method == "POST" {
						if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
							t.Error(err)
							w.WriteHeader(500)
							return
						}
					}
					calls = append(calls, struct {
						Method, URL string
						Body        any
					}{r.Method, source, body})
					raw, ok := c.Pages[source]
					if !ok {
						t.Error("request left reference", source)
						w.WriteHeader(500)
						return
					}
					var response struct {
						Body    string
						Status  int
						Headers map[string]string
					}
					if err := json.Unmarshal(raw, &response); err != nil {
						t.Error(err)
						w.WriteHeader(500)
						return
					}
					for key, value := range response.Headers {
						w.Header().Set(key, value)
					}
					w.WriteHeader(response.Status)
					fmt.Fprint(w, response.Body)
				})
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				if proxy {
					client = credentialedProxyFixture(t, client)
				}
				result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
				status := "succeeded"
				if c.NativeEmpty || c.Expected["title"] == nil && c.Expected["description"] == nil {
					status = "failed"
				}
				if c.NativeReserved {
					status = "publisher_reserved"
				}
				if err != nil || result == nil || !result.Settled || result.Cycle.Status != status || result.HTTP.Requests != int64(len(calls)) {
					t.Fatal("detail did not preserve terminal behavior", result, err, len(calls))
				}
				if len(calls) > len(c.Requests) || !reflect.DeepEqual(calls, c.Requests[:len(calls)]) || !c.NativeReserved && len(calls) != len(c.Requests) {
					t.Fatal("detail request method/source/body differs", calls, c.Requests)
				}
				var titles []string
				var employment *string
				var due time.Time
				var reserved, active bool
				var failures int
				if err := f.pg.QueryRow(ctx, "SELECT titles,employment_type,next_scrape_at,tdm_reserved,is_active,scrape_failures FROM job_posting WHERE id=$1::uuid", f.original).Scan(&titles, &employment, &due, &reserved, &active, &failures); err != nil {
					t.Fatal(err)
				}
				wantTitle := "Monitor title"
				if status == "succeeded" && mode != "description-mask" {
					if title, ok := c.Expected["title"].(string); ok {
						wantTitle = title
					}
				}
				wantFailures := 0
				if status == "failed" {
					wantFailures = 1
				}
				if len(titles) != 1 || titles[0] != wantTitle || reserved != c.NativeReserved || !active || failures != wantFailures {
					t.Fatal("canonical detail title/liveness/failures differ", titles, reserved, active, failures)
				}
				if status != "succeeded" || mode == "description-mask" {
					if employment == nil || *employment != "part_time" {
						t.Fatal("detail overwrote retained employment")
					}
				} else if provider == "mokahr" && (employment == nil || *employment != "full_time") {
					t.Fatal("Moka employment lost")
				}
				var descriptions int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions WHERE posting_id=$1::uuid", f.original).Scan(&descriptions); err != nil {
					t.Fatal(err)
				}
				if status == "succeeded" {
					var html string
					var uploaded bool
					if err := f.pg.QueryRow(ctx, "SELECT html,r2_uploaded FROM descriptions WHERE posting_id=$1::uuid LIMIT 1", f.original).Scan(&html, &uploaded); err != nil || uploaded || !strings.Contains(html, "<p>") {
						t.Fatal("canonical description staging lost", err, html)
					}
					if provider == "mokahr" && !strings.Contains(html, "工作职责") {
						t.Fatal("decrypted description lost")
					}
					if description, ok := c.Expected["description"].(string); provider == "eightfold" && ok && !strings.Contains(html, description) {
						t.Fatal("Eightfold description lost", html)
					}
				} else if descriptions != 0 {
					t.Fatal("failure or reservation wrote content")
				}
				score, err := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
				if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
					t.Fatal("detail deadline or lease differs", err)
				}
				if c.NativeReserved {
					var resource string
					if err := f.pg.QueryRow(ctx, "SELECT tdm_reservation->>'url' FROM job_posting WHERE id=$1::uuid", f.original).Scan(&resource); err != nil || resource != calls[len(calls)-1].URL {
						t.Fatal("reservation resource differs", err, resource)
					}
				}
			})
		}
	}
}
