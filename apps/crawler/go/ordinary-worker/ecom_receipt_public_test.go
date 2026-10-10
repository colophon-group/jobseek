package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func ecomOriginalCapture(t *testing.T) (map[string]string, []lastHTTPExchange, []api.Job, []api.Job) {
	t.Helper()
	dir := os.Getenv("JOBSEEK_RMK_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires private complete original RSS and dispatcher oracle")
	}
	raw, e := os.ReadFile(filepath.Join(dir, "native1005-ecom-retained-receipt-original-dispatch1-2026-10-10.json"))
	if e != nil {
		t.Fatal("private oracle unavailable")
	}
	var c struct {
		Canonical map[string]string
		Jobs      []api.Job
		RawJobs   []api.Job `json:"raw_jobs"`
		Exchanges []lastHTTPExchange
		Truncated bool
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if d.Decode(&c) != nil || len(c.Jobs) != 54 || len(c.RawJobs) != 54 || len(c.Exchanges) != 1 || c.Truncated {
		t.Fatal("complete original oracle unavailable")
	}
	return c.Canonical, c.Exchanges, c.RawJobs, c.Jobs
}
func ecomCompareOriginalFields(t *testing.T, jobs []RichMonitorJob, want []api.Job) {
	t.Helper()
	got := []api.Job{}
	for _, j := range jobs {
		var title, description any
		if j.Title != nil {
			title = *j.Title
		}
		if j.Description != nil {
			description = *j.Description
		}
		got = append(got, api.Job{URL: j.URL, SourceIdentity: j.SourceIdentity, Title: title, Description: description, Locations: j.Locations, DatePosted: j.DatePosted, EmploymentType: j.EmploymentType, JobLocationType: j.JobLocationType, Metadata: j.Metadata, Extras: j.Extras})
	}
	encoded, e := json.Marshal(got)
	if e != nil {
		t.Fatal("native field encoding failed")
	}
	got = nil
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	if decoder.Decode(&got) != nil {
		t.Fatal("native field projection failed")
	}
	for i := range got {
		// The original dataclass encodes an absent location list as null; the native
		// RSS adapter encodes the same empty optional field as []. Compare content.
		if len(got[i].Locations) == 0 {
			got[i].Locations = nil
		}
		if got[i].Metadata == nil {
			got[i].Metadata = map[string]any{}
		}
		if got[i].Extras == nil {
			got[i].Extras = map[string]any{}
		}
	}
	for i := range want {
		if want[i].Metadata == nil {
			want[i].Metadata = map[string]any{}
		}
		if want[i].Extras == nil {
			want[i].Extras = map[string]any{}
		}
	}
	sort.Slice(got, func(i, j int) bool { return got[i].URL < got[j].URL })
	sort.Slice(want, func(i, j int) bool { return want[i].URL < want[j].URL })
	if len(got) != len(want) {
		t.Fatal("original inventory count changed", len(got), len(want))
	}
	if !reflect.DeepEqual(got, want) {
		for i := range got {
			if !reflect.DeepEqual(got[i], want[i]) {
				a, b := reflect.ValueOf(got[i]), reflect.ValueOf(want[i])
				for field := 0; field < a.NumField(); field++ {
					if !reflect.DeepEqual(a.Field(field).Interface(), b.Field(field).Interface()) {
						if a.Type().Field(field).Name == "Locations" {
							t.Log("location projection", "native_count", len(got[i].Locations), "original_count", len(want[i].Locations), "native_nil", got[i].Locations == nil, "original_nil", want[i].Locations == nil)
						}
						t.Log("different original field", a.Type().Field(field).Name, "native_type", fmt.Sprintf("%T", a.Field(field).Interface()), "original_type", fmt.Sprintf("%T", b.Field(field).Interface()))
					}
				}
				break
			}
		}
		t.Fatal("original complete RSS fields or collision identity changed", len(got), len(want))
	}
}
func TestEcomOriginalRSSAndCollisionWithRetainedReceipt(t *testing.T) {
	config, exchanges, rawJobs, want := ecomOriginalCapture(t)
	p, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if e != nil {
		t.Fatal("canonical retained receipt configuration rejected", e)
	}
	calls := 0
	client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		x := exchanges[0]
		if calls != 1 || r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL {
			t.Error("original RSS request changed")
			w.WriteHeader(400)
			return
		}
		w.Header().Set("Content-Type", x.ResponseHeaders["content-type"])
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	out, e := DiscoverRichMonitor(context.Background(), client, p)
	if e != nil || calls != 1 || out.Truncated {
		t.Fatal("original RSS inventory changed", e)
	}
	ecomCompareOriginalFields(t, out.Jobs, rawJobs)
	jobs, e := applyFeedMonitorURLs(context.Background(), config, out.Jobs)
	if e != nil {
		t.Fatal("original collision dispatcher rejected", e)
	}
	ecomCompareOriginalFields(t, jobs, want)
	var metadata map[string]any
	_ = json.Unmarshal([]byte(config["metadata"]), &metadata)
	if metadata["identity_migration"] != nil || metadata["_identity_migration_receipt"] == nil {
		t.Fatal("retained inactive receipt contract changed")
	}
	t.Log("54 original raw and dispatched records match all fields and collision identities; retained receipt cannot activate migration")
}

func TestRealEcomRetainedReceiptPublishesWithoutMigration(t *testing.T) {
	config, exchanges, _, want := ecomOriginalCapture(t)
	raw, e := os.ReadFile(filepath.Join(os.Getenv("JOBSEEK_RMK_PUBLIC_CAPTURE_DIR"), "native1005-ecom-original-normalized-descriptions1-2026-10-10.json"))
	var original struct {
		SourceRevision string `json:"source_revision"`
		Descriptions   map[string]*string
	}
	if e != nil || json.Unmarshal(raw, &original) != nil || original.SourceRevision != "17a3c03eb9e7c053d2b891195993d0d235ff8f54" || len(original.Descriptions) != len(want) {
		t.Fatal("independent original normalization oracle unavailable")
	}
	f := privateRichPipelineFixtureURL(t, "rss", config["metadata"], config["board_url"])
	ctx := context.Background()
	claim, circuits := claimFixture(t, f)
	calls := 0
	var before []byte
	if e := f.pg.QueryRow(ctx, "SELECT metadata->'_identity_migration_receipt' FROM job_board WHERE id=$1::uuid", f.board).Scan(&before); e != nil || len(before) == 0 {
		t.Fatal("retained receipt fixture missing", e)
	}
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		x := exchanges[0]
		if calls != 1 || r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL {
			t.Error("original RSS source request changed")
			w.WriteHeader(400)
			return
		}
		w.Header().Set("Content-Type", x.ResponseHeaders["content-type"])
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
	if e != nil || result == nil || !result.Settled || result.Batches.Inserted != 54 || calls != 1 {
		t.Fatal("complete original collision inventory did not settle", e)
	}
	assertRichDeadlineAndLease(t, f, "rss")
	var after []byte
	var missing, undesiredDetails int
	if e := f.pg.QueryRow(ctx, "SELECT metadata->'_identity_migration_receipt' FROM job_board WHERE id=$1::uuid", f.board).Scan(&after); e != nil {
		t.Fatal(e)
	}
	if !bytes.Equal(before, after) {
		t.Fatal("inert migration receipt changed")
	}
	if e := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
		t.Fatal(e)
	}
	if e := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND next_scrape_at IS NOT NULL", f.board, f.original).Scan(&undesiredDetails); e != nil {
		t.Fatal(e)
	}
	if missing != 1 || undesiredDetails != 0 {
		t.Fatal("original no-enrichment schedule or absence changed", missing, undesiredDetails)
	}
	for _, j := range want {
		var title string
		var description *string
		if e := f.pg.QueryRow(ctx, "SELECT p.titles[1],d.html FROM job_posting p LEFT JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.source_url=$2", f.board, j.URL).Scan(&title, &description); e != nil {
			t.Fatal(e)
		}
		if title != j.Title {
			t.Fatal("original canonical title changed")
		}
		expected, exists := original.Descriptions[j.URL]
		if !exists || !reflect.DeepEqual(description, expected) {
			t.Fatal("original canonical description changed")
		}
	}
	t.Log("54 canonical RSS records and descriptions settled; retained migration receipt unchanged; no unsolicited detail work")
}
