package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestParseFacetCountsRejectsAmbiguousOrMalformedResponses(t *testing.T) {
	valid := []byte(`{"facet_counts":[{"field_name":"location_ids","counts":[{"value":"12","count":4},{"value":"13","count":0}]}]}`)
	got, err := parseFacetCounts(valid, "location_ids")
	if err != nil || !reflect.DeepEqual(got, map[string]int{"12": 4, "13": 0}) {
		t.Fatalf("valid facet = %v, %v", got, err)
	}
	for _, body := range []string{
		`{}`,
		`{"facet_counts":null}`,
		`{"facet_counts":[{"field_name":"other","counts":[]}]}`,
		`{"facet_counts":[{"field_name":"location_ids","counts":[]},{"field_name":"location_ids","counts":[]}]}`,
		`{"facet_counts":[{"field_name":"location_ids","counts":[{"value":"12","count":true}]}]}`,
		`{"facet_counts":[{"field_name":"location_ids","counts":[{"value":"12","count":null}]}]}`,
		`{"facet_counts":[{"field_name":"location_ids","counts":[{"value":null,"count":1}]}]}`,
		`{"facet_counts":[{"field_name":"location_ids","counts":[{"value":"12","count":-1}]}]}`,
		`{"facet_counts":[{"field_name":"location_ids","counts":[{"value":"12","count":1},{"value":"12","count":2}]}]}`,
	} {
		if _, err := parseFacetCounts([]byte(body), "location_ids"); err == nil {
			t.Fatalf("accepted invalid facet response %s", body)
		}
	}
}

func TestFetchFacetCountsUsesWebFilterAndExhaustiveFacet(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/collections/job_posting/documents/search" || r.Header.Get("X-TYPESENSE-API-KEY") != "key" {
			t.Errorf("unexpected Typesense request: %s %s", r.Method, r.URL)
		}
		query := r.URL.Query()
		for key, want := range map[string]string{
			"q": "*", "query_by": "title", "filter_by": postingBaseFilter,
			"facet_by": "location_ids", "max_facet_values": "100000",
			"facet_strategy": "exhaustive", "per_page": "0",
		} {
			if query.Get(key) != want {
				t.Errorf("%s = %q, want %q", key, query.Get(key), want)
			}
		}
		_, _ = io.WriteString(w, `{"facet_counts":[{"field_name":"location_ids","counts":[{"value":"12","count":4}]}]}`)
	}))
	defer server.Close()
	got, err := fetchFacetCounts(context.Background(), server.Client(), server.URL, "key", "location_ids", postingBaseFilter)
	if err != nil || got["12"] != 4 {
		t.Fatalf("fetch facet = %v, %v", got, err)
	}
}

func TestCountDocsAndYearCutoffMatchPythonSemantics(t *testing.T) {
	got := countDocs(map[string]string{"2-en": "2", "1-de": "1"}, map[string]int{"1": 3}, nil, false)
	want := []map[string]any{
		{"id": "1-de", "active_posting_count": 3, "has_active_postings": true},
		{"id": "2-en", "active_posting_count": 0, "has_active_postings": false},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("taxonomy updates = %v", got)
	}
	company := countDocs(map[string]string{"c": "c"}, map[string]int{"c": 8}, map[string]int{"c": 12}, true)
	if !reflect.DeepEqual(company, []map[string]any{{"id": "c", "active_posting_count": 8, "year_posting_count": 12}}) {
		t.Fatalf("company updates = %v", company)
	}
	leap := time.Date(2028, time.February, 29, 12, 34, 56, 0, time.UTC)
	wantCutoff := time.Date(2027, time.February, 28, 12, 34, 56, 0, time.UTC).Unix()
	if oneYearAgoEpoch(leap) != wantCutoff {
		t.Fatalf("leap cutoff = %d, want %d", oneYearAgoEpoch(leap), wantCutoff)
	}
}

func TestCountUpdateRequiresEveryTypesenseAcknowledgement(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/collections/occupation/documents/import" || r.URL.Query().Get("action") != "update" {
			t.Errorf("unexpected import target: %s", r.URL)
		}
		body, _ := io.ReadAll(r.Body)
		var doc map[string]any
		if err := json.Unmarshal(body, &doc); err != nil || doc["id"] != "2-en" {
			t.Errorf("unexpected import body: %s", body)
		}
		_, _ = io.WriteString(w, `{"success":false,"error":"missing document","code":404}`+"\n")
	}))
	defer server.Close()
	failed, err := importCollectionDocs(context.Background(), server.Client(), server.URL, "key", "occupation", "update", []map[string]any{{"id": "2-en", "active_posting_count": 1}})
	if err != nil || !strings.Contains(failed["2-en"].Reason, "missing document") {
		t.Fatalf("rejected update = %v, %v", failed, err)
	}
}

func TestRefreshRetryIsBoundedAndRecoversTransientFailure(t *testing.T) {
	attempts := 0
	value, err := retryRefreshRequest(context.Background(), "facet:test", func() (int, error) {
		attempts++
		if attempts == 1 {
			return 0, io.ErrUnexpectedEOF
		}
		return 42, nil
	})
	if err != nil || value != 42 || attempts != 2 {
		t.Fatalf("retry result = %d, %v after %d attempts", value, err, attempts)
	}
	canceled, stop := context.WithCancel(context.Background())
	stop()
	attempts = 0
	_, err = retryRefreshRequest(canceled, "facet:test", func() (int, error) {
		attempts++
		return 0, io.ErrUnexpectedEOF
	})
	if err == nil || attempts != 1 {
		t.Fatalf("canceled retry made %d attempts: %v", attempts, err)
	}
}
