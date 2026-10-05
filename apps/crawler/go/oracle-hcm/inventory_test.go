package oraclehcm

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"testing"
)

const oracleBoard = "https://acme.fa.eu2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs"

func testOptions(t *testing.T, metadata map[string]any) Options {
	t.Helper()
	o, err := OptionsFromMetadata(oracleBoard, metadata)
	if err != nil {
		t.Fatal(err)
	}
	return o
}

func page(t *testing.T, total int, ids []int, extra map[string]any) []byte {
	t.Helper()
	rows := []any{}
	for _, id := range ids {
		rows = append(rows, map[string]any{"Id": id, "Title": "Engineer", "PrimaryLocation": "Zurich", "PostedDate": "2026-10-05", "JobSchedule": "Full time"})
	}
	w := map[string]any{"TotalJobsCount": total, "requisitionList": rows}
	for k, v := range extra {
		w[k] = v
	}
	b, err := json.Marshal(map[string]any{"items": []any{w}})
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func ids(start, end int) []int {
	v := []int{}
	for i := start; i < end; i++ {
		v = append(v, i)
	}
	return v
}

func TestDiscoverCompoundOffsetsAndRichFields(t *testing.T) {
	o := testOptions(t, nil)
	calls := []string{}
	got, err := Discover(context.Background(), o, func(_ context.Context, url string) ([]byte, error) {
		calls = append(calls, url)
		if len(calls) == 1 {
			return page(t, 201, ids(1, 201), nil), nil
		}
		if url != o.Endpoint()+",offset=200" {
			t.Fatalf("offset must belong to Oracle's compound finder: %s", url)
		}
		return page(t, 201, []int{201}, nil), nil
	})
	if err != nil || got.Truncated || len(got.Jobs) != 201 || len(calls) != 2 {
		t.Fatalf("inventory %d, truncated %v, calls %d, error %v", len(got.Jobs), got.Truncated, len(calls), err)
	}
	j := got.Jobs[200]
	if j.URL != o.JobURL("201") || j.Title != "Engineer" || j.DatePosted != "2026-10-05" || j.EmploymentType != "Full time" || len(j.Locations) != 1 || j.Locations[0] != "Zurich" {
		t.Fatalf("rich fields lost: %+v", j)
	}
}

func TestDiscoverLaterFailureCannotPublishPartialInventory(t *testing.T) {
	cause := errors.New("upstream failed")
	calls := 0
	got, err := Discover(context.Background(), testOptions(t, nil), func(context.Context, string) ([]byte, error) {
		calls++
		if calls == 1 {
			return page(t, 201, ids(1, 201), nil), nil
		}
		return nil, cause
	})
	if !errors.Is(err, cause) || len(got.Jobs) != 0 {
		t.Fatalf("later failure supplied publishable inventory: %+v %v", got, err)
	}
}

func TestDiscoverIncompleteInventoryEvidence(t *testing.T) {
	for _, test := range []struct {
		name      string
		total     int
		ids       []int
		metadata  map[string]any
		truncated bool
	}{
		{"short final page", 3, []int{1, 2}, nil, true},
		{"shortfall tolerance", 3, []int{1, 2}, map[string]any{"total_count_tolerance": 1}, false},
		{"duplicate row", 2, []int{1, 1}, nil, true},
		{"duplicate tolerance plus count tolerance", 2, []int{1, 1}, map[string]any{"duplicate_row_tolerance": 1, "total_count_tolerance": 1}, false},
		{"missing ID", 2, []int{1, 0}, nil, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			got, err := Discover(context.Background(), testOptions(t, test.metadata), func(context.Context, string) ([]byte, error) { return page(t, test.total, test.ids, nil), nil })
			if err != nil || got.Truncated != test.truncated {
				t.Fatalf("truncated %v, error %v", got.Truncated, err)
			}
		})
	}
}

func TestDiscoverOrganizationRequiresAppliedFacetProof(t *testing.T) {
	for _, valid := range []bool{true, false} {
		t.Run(fmt.Sprint(valid), func(t *testing.T) {
			o := testOptions(t, map[string]any{"organization_id": "42"})
			selected := "42"
			if !valid {
				selected = "43"
			}
			got, err := Discover(context.Background(), o, func(_ context.Context, url string) ([]byte, error) {
				if !strings.HasSuffix(url, ",selectedOrganizationsFacet=42") {
					t.Fatal("organization not bound")
				}
				return page(t, 1, []int{1}, map[string]any{"SelectedOrganizationsFacet": selected, "organizationsFacet": []any{map[string]any{"Id": "42", "TotalCount": 1}}}), nil
			})
			if valid && (err != nil || got.Truncated || len(got.Jobs) != 1) || !valid && (!errors.Is(err, ErrInventory) || len(got.Jobs) != 0) {
				t.Fatalf("proof outcome %+v %v", got, err)
			}
		})
	}
}

func TestDiscoverRejectsUnexpectedLocationShapes(t *testing.T) {
	for _, location := range []any{map[string]any{"name": "Zurich"}, []any{"Zurich"}} {
		b, _ := json.Marshal(map[string]any{"items": []any{map[string]any{"TotalJobsCount": 1, "requisitionList": []any{map[string]any{"Id": 1, "PrimaryLocation": location}}}}})
		got, err := Discover(context.Background(), testOptions(t, nil), func(context.Context, string) ([]byte, error) { return b, nil })
		if !errors.Is(err, ErrInventory) || len(got.Jobs) != 0 {
			t.Fatalf("invalid location published: %+v %v", got, err)
		}
	}
}

func TestDiscoverContextCancellationPrecedesFetch(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := Discover(ctx, testOptions(t, nil), func(context.Context, string) ([]byte, error) { t.Fatal("cancelled inventory fetched"); return nil, nil })
	if !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}

func TestDiscoverOverlapAbsorbsSmallTotalDrop(t *testing.T) {
	for _, overlap := range []int{0, 2} {
		calls := 0
		got, err := Discover(context.Background(), testOptions(t, map[string]any{"offset_overlap": overlap}), func(_ context.Context, url string) ([]byte, error) {
			calls++
			if calls == 1 {
				return page(t, 201, ids(1, 201), nil), nil
			}
			if !strings.HasSuffix(url, ",offset="+strconv.Itoa(200-overlap)) {
				t.Fatal("overlap not applied")
			}
			return page(t, 200, ids(201-overlap, 201), nil), nil
		})
		if err != nil || len(got.Jobs) != 200 || got.Truncated != (overlap == 0) {
			t.Fatalf("overlap %d count %d truncated %v error %v", overlap, len(got.Jobs), got.Truncated, err)
		}
	}
}

func TestDiscoverFacetPartitionAvoidsOracleWindowLimit(t *testing.T) {
	o := testOptions(t, nil)
	calls := 0
	got, err := Discover(context.Background(), o, func(_ context.Context, url string) ([]byte, error) {
		calls++
		if url == o.Endpoint() {
			return page(t, 10001, nil, map[string]any{"categoriesFacet": []any{map[string]any{"Id": "a", "TotalCount": 6000}, map[string]any{"Id": "b", "TotalCount": 4001}}}), nil
		}
		start, count := 1, 6000
		if strings.Contains(url, ",selectedCategoriesFacet=b") {
			start, count = 6001, 4001
		} else if !strings.Contains(url, ",selectedCategoriesFacet=a") {
			t.Fatal("unbound facet")
		}
		offset := 0
		if _, v, ok := strings.Cut(url, ",offset="); ok {
			offset, _ = strconv.Atoi(v)
		}
		if offset >= 10000 {
			t.Fatal("Oracle result window crossed")
		}
		return page(t, count, ids(start+offset, start+min(offset+200, count)), nil), nil
	})
	if err != nil || got.Truncated || len(got.Jobs) != 10001 || calls != 52 {
		t.Fatalf("partition result count %d calls %d truncated %v error %v", len(got.Jobs), calls, got.Truncated, err)
	}
}

func TestFacetPartitionRequiresCompleteNonoverlappingCounts(t *testing.T) {
	w := map[string]any{"categoriesFacet": []any{map[string]any{"Id": "a", "TotalCount": 10001}}, "organizationsFacet": []any{map[string]any{"Id": "a", "TotalCount": 6000}, map[string]any{"Id": "b", "TotalCount": 4001}}}
	p := partitions(w, 10001)
	if len(p) != 2 || p[0].parameter != "selectedOrganizationsFacet" {
		t.Fatal("valid organization fallback lost")
	}
	w["organizationsFacet"] = []any{map[string]any{"Id": "a", "TotalCount": 6000}, map[string]any{"Id": "a", "TotalCount": 4001}}
	if partitions(w, 10001) != nil {
		t.Fatal("duplicate facet admitted")
	}
}
