package worker

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

func TestRealAPILegacyLiteralsCanonicalFieldsAndFailureAuthority(t *testing.T) {
	for _, mode := range []string{"complete", "query-error", "transport-failure"} {
		t.Run(mode, func(t *testing.T) {
			md := map[string]any{"api_url": "https://example.com/api", "json_path": "jobs[?contains(city, `Geneva`) || contains(city, `Genève`)].{url:link,title:name,description:body,locations:[city]}", "url_field": "url", "fields": map[string]any{"title": "title", "description": "description", "locations": "locations"}, "scraper_type": "skip"}
			raw, _ := json.Marshal(md)
			f := privateRichPipelineFixture(t, "api_sniffer", string(raw))
			ctx := context.Background()
			source := "https://example.com/jobs/" + f.company
			claim, circuits := claimFixture(t, f)
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if mode == "transport-failure" {
					w.WriteHeader(400)
					return
				}
				var city any = "Geneva"
				if mode == "query-error" {
					city = nil
				}
				json.NewEncoder(w).Encode(map[string]any{"jobs": []any{map[string]any{"link": source, "name": "Software Engineer", "body": "<p>Build reliable Go systems.</p>", "city": city}}})
			}))
			if mode == "transport-failure" {
				client.client.Transport = workdayDetailRoundTrip(func(*http.Request) (*http.Response, error) { return nil, x509.UnknownAuthorityError{} })
			}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("legacy literal cycle did not settle", err, result)
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer")
			var failures, empty, gone int
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,empty_check_count,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &empty, &gone); err != nil {
				t.Fatal(err)
			}
			if mode != "complete" {
				var active bool
				f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active)
				if failures != 1 || empty != 0 || gone != 0 || !active || result.Batches.Inserted != 0 {
					t.Fatal("failed query/transport acquired absence authority", result, failures, empty, gone)
				}
				return
			}
			var title, body string
			if err := f.pg.QueryRow(ctx, "SELECT p.titles[1],d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.source_url=$2", f.board, source).Scan(&title, &body); err != nil || title != "Software Engineer" || body != "<p>Build reliable Go systems.</p>" || result.Batches.Inserted != 1 || failures != 0 {
				t.Fatal("legacy expression changed canonical fields", err, fmt.Sprint(result))
			}
		})
	}
}

func TestPublicGoNETVerifiedCapture(t *testing.T) {
	output := os.Getenv("JOBSEEK_GONET_VERIFIED_CAPTURE_FILE")
	if output == "" {
		t.Skip("explicit bounded private public capture not requested")
	}
	raw, err := os.ReadFile(os.Getenv("JOBSEEK_INLINE_PUBLIC_CAPTURE_DIR") + "/native1001-shared-gonet-careers-public-capture4-2026-10-10.json")
	if err != nil {
		t.Fatal("original diagnostic unavailable")
	}
	var c struct{ Board map[string]json.RawMessage }
	if json.Unmarshal(raw, &c) != nil {
		t.Fatal("original configuration unavailable")
	}
	config := map[string]string{}
	for k, v := range c.Board {
		if k == "metadata" {
			config[k] = string(v)
		} else {
			var s string
			if json.Unmarshal(v, &s) == nil {
				config[k] = s
			}
		}
	}
	p, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if err != nil {
		t.Fatal(err)
	}
	exchanges := []map[string]any{}
	client := &http.Client{Timeout: 30 * time.Second, Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		response, err := http.DefaultTransport.RoundTrip(r)
		if err != nil {
			return nil, err
		}
		body, err := io.ReadAll(io.LimitReader(response.Body, (64<<20)+1))
		response.Body.Close()
		if err != nil || len(body) > 64<<20 {
			return nil, fmt.Errorf("bounded public response failed")
		}
		response.Body = io.NopCloser(strings.NewReader(string(body)))
		exchanges = append(exchanges, map[string]any{"method": r.Method, "url": r.URL.String(), "status": response.StatusCode, "content_type": response.Header.Get("Content-Type"), "body": string(body), "request_body": ""})
		return response, nil
	})}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	found, err := discoverAPISnifferInventory(ctx, client, p, config)
	if err != nil || found.Truncated || len(found.Jobs) == 0 || len(exchanges) != 1 {
		t.Fatal("complete verified public inventory unavailable", err)
	}
	record := map[string]any{"provider": "api_sniffer", "status": "complete", "board": c.Board, "exchanges": exchanges, "go_job_count": len(found.Jobs), "verified_tls": true, "platform": "macos-system-roots", "scope": "actual Go verified public response; original Python replay and field parity still required"}
	body, _ := json.Marshal(record)
	f, err := os.OpenFile(output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		t.Fatal("protected public capture unavailable")
	}
	defer f.Close()
	if _, err = f.Write(body); err != nil {
		t.Fatal("protected capture write failed")
	}
	if f.Sync() != nil {
		t.Fatal("protected capture sync failed")
	}
}

func TestPublicGoNETOriginalFieldsFromVerifiedCapture(t *testing.T) {
	directory := os.Getenv("JOBSEEK_INLINE_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("private original field replay not supplied")
	}
	raw, err := os.ReadFile(directory + "/native1001-shared-gonet-careers-public-replay1-2026-10-10.json")
	if err != nil {
		t.Fatal("original field replay unavailable")
	}
	var c struct {
		Board     map[string]json.RawMessage
		Jobs      []map[string]any
		Exchanges []struct {
			Method, URL, Body string
			Status            int
			ContentType       string `json:"content_type"`
		}
		Status string
	}
	if json.Unmarshal(raw, &c) != nil || c.Status != "complete" || len(c.Exchanges) != 1 {
		t.Fatal("verified original public capture invalid")
	}
	config := map[string]string{}
	for k, v := range c.Board {
		if k == "metadata" {
			config[k] = string(v)
		} else {
			var s string
			if json.Unmarshal(v, &s) == nil {
				config[k] = s
			}
		}
	}
	p, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		x := c.Exchanges[0]
		if calls != 1 || r.Method != x.Method || "https://"+r.Host+r.URL.RequestURI() != x.URL {
			t.Error("original configured API request changed")
		}
		w.Header().Set("Content-Type", x.ContentType)
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	got, err := discoverAPISnifferInventory(context.Background(), client.client, p, config)
	if err != nil || got.Truncated || len(got.Jobs) != len(c.Jobs) || calls != 1 {
		t.Fatal("complete configured API inventory changed", err)
	}
	sort.Slice(got.Jobs, func(i, j int) bool { return got.Jobs[i].URL < got.Jobs[j].URL })
	for n, j := range got.Jobs {
		fields := map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "metadata": j.Metadata, "employment_type": j.EmploymentType, "date_posted": j.DatePosted}
		b, _ := json.Marshal(fields)
		var actual map[string]any
		json.Unmarshal(b, &actual)
		for k, want := range c.Jobs[n] {
			if !reflect.DeepEqual(actual[k], want) {
				t.Fatal("original configured API field changed", n, k)
			}
		}
	}
}
