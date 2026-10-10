package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

func TestWorkdayExplicitTLSOriginalPublicInventoryAndDetails(t *testing.T) {
	dir := os.Getenv("JOBSEEK_WORKDAY_TLS_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original functions and explicit canonical TLS capture")
	}
	raw, e := os.ReadFile(filepath.Join(dir, "native1007-workday-tls-wachtell-careers-workday-original-public-capture2-2026-10-10.json"))
	if e != nil {
		t.Fatal("protected public capture unavailable")
	}
	var c struct {
		Board     map[string]any
		Jobs      []struct{ URL string }
		Status    string
		Exchanges []struct {
			Method, URL, Body string
			Status            int
			RequestBody       string            `json:"request_body"`
			ResponseHeaders   map[string]string `json:"response_headers"`
		}
		MonitorExchangesCount int `json:"monitor_exchanges_count"`
		DetailSamples         []struct {
			URL     string
			Content map[string]any
		} `json:"detail_samples"`
	}
	if json.Unmarshal(raw, &c) != nil || c.Status != "complete" || len(c.Jobs) != 8 || len(c.DetailSamples) != 5 || c.MonitorExchangesCount != 2 || len(c.Exchanges) != 7 {
		t.Fatal("complete explicit TLS source unavailable")
	}
	config := map[string]string{}
	for k, v := range c.Board {
		if k == "metadata" {
			body, _ := json.Marshal(v)
			config[k] = string(body)
		} else {
			config[k] = fmt.Sprint(v)
		}
	}
	if _, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config); e != nil {
		t.Fatal("canonical Workday configuration rejected", e)
	}
	if skip, e := queue.MonitorSkipsSSL(config); e != nil || !skip {
		t.Fatal("explicit monitor exception not bound", e)
	}
	options, e := workday.ParseInventoryConfig(config["board_url"], c.Board["metadata"].(map[string]any))
	if e != nil {
		t.Fatal(e)
	}
	calls := 0
	client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls >= len(c.Exchanges) {
			t.Error("extra Workday request")
			w.WriteHeader(400)
			return
		}
		x := c.Exchanges[calls]
		calls++
		if r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL {
			t.Error("original request URL/method changed")
		}
		body, e := io.ReadAll(r.Body)
		if e != nil {
			t.Error(e)
		}
		if x.Method == "POST" {
			var got, want any
			if json.Unmarshal(body, &got) != nil || json.Unmarshal([]byte(x.RequestBody), &want) != nil || !reflect.DeepEqual(got, want) {
				t.Error("original Workday JSON request changed")
			}
		}
		for k, v := range x.ResponseHeaders {
			w.Header().Set(k, v)
		}
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	sealed := &VerifiedDirectHTTP{client: client, skipSSL: true}
	inventory, e := DiscoverWorkdayInventory(context.Background(), sealed, options)
	if e != nil || inventory.Truncated || calls != c.MonitorExchangesCount {
		t.Fatal("complete original Workday inventory changed", e, calls)
	}
	got, want := append([]string{}, inventory.URLs...), []string{}
	for _, j := range c.Jobs {
		want = append(want, j.URL)
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatal("original Workday inventory identities changed")
	}
	for _, sample := range c.DetailSamples {
		result, e := FetchWorkdayDetail(context.Background(), sealed, sample.URL, nil)
		if e != nil || result.Gone {
			t.Fatal("original detail failed", e)
		}
		body, e := json.Marshal(result.Content)
		if e != nil {
			t.Fatal(e)
		}
		var actual map[string]any
		if json.Unmarshal(body, &actual) != nil {
			t.Fatal("detail projection unavailable")
		}
		for _, field := range []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "metadata"} {
			if !reflect.DeepEqual(actual[field], sample.Content[field]) {
				t.Errorf("original detail field changed: %s", field)
			}
		}
	}
	if calls != len(c.Exchanges) {
		t.Fatal("original detail request count changed")
	}
	t.Log("all eight original URLs and five original detail projections match")
}

func TestWorkdayLivePublicConfiguredTLSInventory(t *testing.T) {
	if os.Getenv("JOBSEEK_WORKDAY_TLS_LIVE_PUBLIC") != "1" {
		t.Skip("requires explicit bounded live public qualification")
	}
	dir := os.Getenv("JOBSEEK_WORKDAY_TLS_PUBLIC_CAPTURE_DIR")
	raw, e := os.ReadFile(filepath.Join(dir, "native1007-workday-tls-wachtell-careers-workday-original-public-capture2-2026-10-10.json"))
	if e != nil {
		t.Fatal("original configured TLS capture unavailable")
	}
	var c struct {
		Board  map[string]any
		Jobs   []struct{ URL string }
		Status string
	}
	if json.Unmarshal(raw, &c) != nil || c.Status != "complete" || len(c.Jobs) != 8 {
		t.Fatal("complete original reference unavailable")
	}
	md := c.Board["metadata"].(map[string]any)
	if md["ssl_verify"] != false {
		t.Fatal("original explicit exception absent")
	}
	options, e := workday.ParseInventoryConfig(c.Board["board_url"].(string), md)
	if e != nil {
		t.Fatal(e)
	}
	bundle, e := os.ReadFile(os.Getenv("JOBSEEK_WORKDAY_TLS_CA_FILE"))
	if e != nil {
		t.Fatal("pinned public CA unavailable")
	}
	hash := sha256.Sum256(bundle)
	if hex.EncodeToString(hash[:]) != pinnedCASHA256 {
		t.Fatal("public CA pin differs")
	}
	client, e := NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: bundle, EnableHTTP2: true, SkipSSL: true})
	if e != nil {
		t.Fatal(e)
	}
	defer client.CloseIdleConnections()
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	result, e := DiscoverWorkdayInventory(ctx, client, options)
	if e != nil || result.Truncated {
		t.Fatal("live configured Go HTTP2 inventory failed", e)
	}
	got, want := append([]string{}, result.URLs...), []string{}
	for _, j := range c.Jobs {
		want = append(want, j.URL)
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatal("live configured original inventory changed", len(got), len(want))
	}
	t.Log("actual Go HTTP2 configured TLS exception matches all8 original URLs")
}
