package apisniffer

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"slices"
	"strconv"
	"sync"
	"testing"
	"time"
)

const sessionBrassBoard = "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=5998"
const sessionNjoynBoard = "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&Page=JobListing"

type originalSessionFixture struct {
	BrassBoards []struct {
		Source   string
		Expected []string
	} `json:"brass_boards"`
	BrassJobs []struct {
		Name     string
		Row      json.RawMessage
		Expected json.RawMessage
	} `json:"brass_jobs"`
	BrassDetails []struct {
		Name, Body string
		Expected   []string
		Error      bool
	} `json:"brass_details"`
	BrassPages []struct {
		Payload  json.RawMessage
		Expected json.RawMessage
		Error    bool
	} `json:"brass_pages"`
	NjoynURLs []struct {
		Source   string
		Expected bool
	} `json:"njoyn_urls"`
	NjoynSnapshots []struct {
		Name, Text, PageNumber string
		Links                  []string
		Error                  bool
		SnapshotChange         bool `json:"snapshot_change"`
		Expected               *struct {
			URLs       []string
			Total      int
			PageNumber int `json:"page_number"`
			PageCount  int `json:"page_count"`
		}
	} `json:"njoyn_snapshots"`
	NjoynShapes []struct {
		Name                      string
		Total, Count, Size, Limit int
		PageNumber                int `json:"page_number"`
		PageCount                 int `json:"page_count"`
		Error                     bool
		SnapshotChange            bool `json:"snapshot_change"`
	} `json:"njoyn_shapes"`
	NjoynReconcile []struct {
		Name         string
		FirstURLs    []string `json:"first_urls"`
		SecondURLs   []string `json:"second_urls"`
		FirstTotals  []int    `json:"first_totals"`
		SecondTotals []int    `json:"second_totals"`
		Expected     []string
		Error        bool
	} `json:"njoyn_reconcile"`
	Normalizations map[string]*string
}

func originalSessionCases(t *testing.T) originalSessionFixture {
	t.Helper()
	body, err := os.ReadFile("testdata/python_session_provider_core.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture originalSessionFixture
	if err := json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestBrassRingFieldsAndPagesMatchDeliveredPython(t *testing.T) {
	fixture := originalSessionCases(t)
	for _, c := range fixture.BrassBoards {
		board, err := BrassRingBoardFromURL(c.Source)
		if c.Expected == nil {
			if err == nil {
				t.Fatal("unrecognized board accepted")
			}
			continue
		}
		if err != nil || board.PartnerID != c.Expected[0] || board.SiteID != c.Expected[1] {
			t.Fatal("board identity differs", err)
		}
	}
	board, _ := BrassRingBoardFromURL(sessionBrassBoard)
	normalize := func(raw string) (*string, error) {
		v, ok := fixture.Normalizations[raw]
		if !ok {
			t.Fatalf("normalizer input changed: %q", raw)
		}
		return v, nil
	}
	for _, c := range fixture.BrassJobs {
		t.Run(c.Name, func(t *testing.T) {
			d, err := Decode(c.Row)
			if err != nil {
				t.Fatal(err)
			}
			job, err := BrassRingJob(d.Value, board, normalize)
			if bytes.Equal(c.Expected, []byte("null")) {
				if err == nil {
					t.Fatal("invalid requisition accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			var expected map[string]any
			if json.Unmarshal(c.Expected, &expected) != nil {
				t.Fatal("invalid original job")
			}
			actual := map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "date_posted": job.DatePosted, "metadata": job.Metadata}
			body, _ := json.Marshal(actual)
			var normalized map[string]any
			_ = json.Unmarshal(body, &normalized)
			if !reflect.DeepEqual(expected, normalized) {
				t.Fatalf("original fields differ: expected=%s actual=%s", c.Expected, body)
			}
		})
	}
	for _, c := range fixture.BrassDetails {
		t.Run("detail/"+c.Name, func(t *testing.T) {
			actual, err := BrassRingDetailLocation(c.Body, "3383626")
			if (err != nil) != c.Error || err == nil && !slices.Equal(actual, c.Expected) {
				t.Fatal("detail preload identity/locations differ", err)
			}
		})
	}
	for i, c := range fixture.BrassPages {
		t.Run("page/"+strconv.Itoa(i), func(t *testing.T) {
			d, err := Decode(c.Payload)
			if err != nil {
				t.Fatal(err)
			}
			total, rows, err := BrassRingPage(d)
			if (err != nil) != c.Error {
				t.Fatal("page shape differs", err)
			}
			if err != nil {
				return
			}
			actual, _ := json.Marshal([]any{total, rows})
			if !bytes.Equal(compactJSON(c.Expected), compactJSON(actual)) {
				t.Fatal("original page differs")
			}
		})
	}
}

func compactJSON(body []byte) []byte {
	var out bytes.Buffer
	_ = json.Compact(&out, body)
	return out.Bytes()
}

func TestNjoynIdentitiesAndReconciliationMatchDeliveredPython(t *testing.T) {
	fixture := originalSessionCases(t)
	board, err := NjoynBoardFromURL(sessionNjoynBoard)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range fixture.NjoynURLs {
		if NjoynDetailMatches(board, c.Source) != c.Expected {
			t.Fatal("posting tenant/identity differs", c.Source)
		}
	}
	for _, c := range fixture.NjoynSnapshots {
		t.Run("snapshot/"+c.Name, func(t *testing.T) {
			actual, err := NjoynSnapshotFromState(board, c.Text, c.PageNumber, c.Links)
			if (err != nil) != c.Error || errors.Is(err, ErrNjoynSnapshotChanged) != c.SnapshotChange {
				t.Fatal("snapshot failure class differs", err)
			}
			if err == nil && (actual.Total != c.Expected.Total || actual.PageNumber != c.Expected.PageNumber || actual.PageCount != c.Expected.PageCount || !slices.Equal(actual.URLs, c.Expected.URLs)) {
				t.Fatal("snapshot state differs")
			}
		})
	}
	for _, c := range fixture.NjoynShapes {
		t.Run("shape/"+c.Name, func(t *testing.T) {
			urls := make([]string, c.Count)
			for i := range urls {
				urls[i] = strconv.Itoa(i)
			}
			err := NjoynValidatePageShape(NjoynSnapshot{urls, c.Total, c.PageNumber, c.PageCount}, c.Size, c.Limit)
			if (err != nil) != c.Error || errors.Is(err, ErrNjoynSnapshotChanged) != c.SnapshotChange {
				t.Fatal("page shape differs", err)
			}
		})
	}
	for _, c := range fixture.NjoynReconcile {
		t.Run("reconcile/"+c.Name, func(t *testing.T) {
			actual, err := NjoynReconcile(NjoynPass{c.FirstURLs, c.FirstTotals, 1}, NjoynPass{c.SecondURLs, c.SecondTotals, 1})
			if (err != nil) != c.Error || err == nil && !slices.Equal(actual, c.Expected) {
				t.Fatal("original bounded union differs", err)
			}
		})
	}
}

func sessionBrassRow(id string, location bool) any {
	q := []any{map[string]any{"QuestionName": "reqid", "Value": id}, map[string]any{"QuestionName": "jobtitle", "Value": "Engineer"}, map[string]any{"QuestionName": "jobdescription", "Value": "Build"}}
	if location {
		q = append(q, map[string]any{"QuestionName": "location", "Value": "Zürich"})
	}
	return map[string]any{"Questions": q, "Link": "https://sjobs.brassring.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25416&siteid=5998&jobid=" + id}
}
func sessionBrassPage(total int, rows ...any) *Document {
	body, _ := json.Marshal(map[string]any{"JobsCount": total, "Jobs": map[string]any{"Job": rows}})
	d, _ := Decode(body)
	return d
}

func TestBrassRingCompleteStableSnapshotPrecedesHydration(t *testing.T) {
	board, _ := BrassRingBoardFromURL(sessionBrassBoard)
	for _, mode := range []string{"stable", "changed-total", "duplicate", "short-tail", "missing-location", "malformed-late"} {
		t.Run(mode, func(t *testing.T) {
			var calls []string
			hydrated := 0
			controls := BrassRingControls{
				Search: func(context.Context) (*Document, error) {
					calls = append(calls, "search")
					return sessionBrassPage(3, sessionBrassRow("99", true), sessionBrassRow("98", true)), nil
				},
				Sort: func(context.Context) (*Document, error) {
					calls = append(calls, "sort")
					return sessionBrassPage(3, sessionBrassRow("1", mode != "missing-location"), sessionBrassRow("2", true)), nil
				},
				Next: func(_ context.Context, page int) (*Document, error) {
					if page != 2 {
						t.Fatal("unexpected next index")
					}
					calls = append(calls, "next2")
					total, id := 3, "3"
					if mode == "changed-total" {
						total = 4
					}
					if mode == "duplicate" {
						id = "1"
					}
					if mode == "short-tail" {
						return sessionBrassPage(total), nil
					}
					if mode == "malformed-late" {
						return sessionBrassPage(total, map[string]any{}), nil
					}
					return sessionBrassPage(total, sessionBrassRow(id, true)), nil
				},
			}
			normalize := func(s string) (*string, error) { return &s, nil }
			result, err := DiscoverBrassRing(context.Background(), board, controls, normalize, func(context.Context, string, string) ([]string, error) { hydrated++; return nil, ErrInventory })
			if mode == "stable" {
				if err != nil || len(result.Jobs) != 3 || result.Jobs[0].Metadata["requisition_id"] != "1" {
					t.Fatal("stable sorted inventory failed", err)
				}
			} else {
				if err == nil || len(result.Jobs) != 0 {
					t.Fatal("partial inventory accepted")
				}
			}
			if !slices.Equal(calls, []string{"search", "sort", "next2"}) {
				t.Fatal("stable search/sort/page order changed")
			}
			if mode != "missing-location" && hydrated != 0 {
				t.Fatal("hydration preceded complete inventory validation")
			}
		})
	}
}

func TestBrassRingHydrationKeepsEightRequestBound(t *testing.T) {
	jobs := make([]Job, 33)
	for i := range jobs {
		jobs[i] = Job{URL: strconv.Itoa(i), Metadata: map[string]any{"requisition_id": strconv.Itoa(i)}}
	}
	var mu sync.Mutex
	active, peak := 0, 0
	err := brassRingHydrate(context.Background(), jobs, func(context.Context, string, string) ([]string, error) {
		mu.Lock()
		active++
		peak = max(peak, active)
		mu.Unlock()
		time.Sleep(time.Millisecond)
		mu.Lock()
		active--
		mu.Unlock()
		return []string{"Zürich"}, nil
	})
	if err != nil || peak > 8 || peak < 2 {
		t.Fatal("hydration concurrency changed", peak, err)
	}
	for _, job := range jobs {
		if !slices.Equal(job.Locations, []string{"Zürich"}) {
			t.Fatal("hydration omitted a job")
		}
	}
}

func TestNjoynCompletePairAndExactPageRetry(t *testing.T) {
	for _, mode := range []string{"exact", "transition-retry", "tail-count", "fingerprint-drift", "publisher-error"} {
		t.Run(mode, func(t *testing.T) {
			passes, submissions := 0, 0
			controls := NjoynControls{Wait: func(context.Context, time.Duration) error { return nil }}
			controls.First = func(context.Context) (NjoynSnapshot, error) {
				passes++
				if mode == "fingerprint-drift" && passes%2 == 0 {
					return NjoynSnapshot{[]string{"d", "e"}, 3, 1, 2}, nil
				}
				return NjoynSnapshot{[]string{"a", "b"}, 3, 1, 2}, nil
			}
			controls.Page = func(context.Context, int) (NjoynSnapshot, error) {
				submissions++
				if mode == "publisher-error" {
					return NjoynSnapshot{}, errors.New("publisher reserved")
				}
				if mode == "transition-retry" && submissions == 1 {
					return NjoynSnapshot{[]string{"a", "b"}, 3, 1, 2}, nil
				}
				tail := []string{"c"}
				if mode == "tail-count" {
					tail = []string{"c", "d"}
				}
				if mode == "fingerprint-drift" {
					if passes%2 == 0 {
						return NjoynSnapshot{[]string{"f"}, 3, 2, 2}, nil
					}
				}
				return NjoynSnapshot{tail, 3, 2, 2}, nil
			}
			result, err := DiscoverNjoyn(context.Background(), controls, 200, 2)
			if mode == "exact" || mode == "transition-retry" {
				if err != nil || len(result.Jobs) != 3 || passes != 2 || !result.URLOnly {
					t.Fatal("complete pair did not settle", err)
				}
			} else {
				if err == nil || len(result.Jobs) != 0 {
					t.Fatal("incomplete pair accepted")
				}
			}
			if mode == "transition-retry" && submissions != 3 {
				t.Fatal("exact-page retry was not bounded")
			}
			if mode == "publisher-error" && passes != 1 {
				t.Fatal("publisher failure restarted a snapshot")
			}
		})
	}
}
