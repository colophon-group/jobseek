package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"testing"
)

func TestSyncProjectionMatchesPythonProducer(t *testing.T) {
	data, err := os.ReadFile("testdata/sync_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Input        taxonomyInputs            `json:"input"`
		Companies    []syncCompanyRow          `json:"companies"`
		Descriptions []syncCompanyDescription  `json:"descriptions"`
		Counts       map[string]map[string]int `json:"counts"`
		Year         map[string]int            `json:"year"`
		Documents    taxonomyDocuments         `json:"documents"`
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	contract, _ := loadTaxonomyContract()
	docs, err := buildSyncTaxonomies(fixture.Input, contract, fixture.Companies, fixture.Descriptions)
	if err != nil {
		t.Fatal(err)
	}
	applySyncCounts(docs, fixture.Counts, fixture.Year)
	for _, documents := range docs {
		sort.Slice(documents, func(i, j int) bool { return documents[i]["id"].(string) < documents[j]["id"].(string) })
	}
	actual, err := taxonomyCanonicalJSON(docs, false)
	if err != nil {
		t.Fatal(err)
	}
	expected, err := taxonomyCanonicalJSON(fixture.Documents, false)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(actual, expected) {
		t.Fatalf("Python/Go producer mismatch\nGo: %s\nPython: %s", actual, expected)
	}
}
func TestCompanyCensusRequiresExactPages(t *testing.T) {
	for _, kind := range []string{"complete", "short", "duplicate", "drift", "wrong_metadata"} {
		t.Run(kind, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if !strings.HasSuffix(r.URL.Path, "/search") {
					count := 251
					if kind == "wrong_metadata" {
						count = 250
					}
					json.NewEncoder(w).Encode(map[string]any{"num_documents": count})
					return
				}
				if r.URL.Query().Get("enable_overrides") != "false" || r.URL.Query().Get("per_page") != "250" || r.URL.Query().Get("include_fields") != "id" {
					t.Error("census request changed")
				}
				hits := []map[string]any{}
				page := r.URL.Query().Get("page")
				found := 251
				if page == "1" {
					limit := 250
					if kind == "short" {
						limit = 249
					}
					for i := 0; i < limit; i++ {
						hits = append(hits, map[string]any{"document": map[string]any{"id": fmt.Sprint(i)}})
					}
				} else {
					id := "last"
					if kind == "duplicate" {
						id = "0"
					}
					hits = append(hits, map[string]any{"document": map[string]any{"id": id}})
					if kind == "drift" {
						found = 252
					}
				}
				json.NewEncoder(w).Encode(map[string]any{"found": found, "hits": hits})
			}))
			defer server.Close()
			ids, err := fetchCompanyIDs(context.Background(), taxonomyReader{Client: server.Client(), BaseURL: server.URL, Key: "fixture"})
			if kind == "complete" {
				if err != nil || len(ids) != 251 {
					t.Fatalf("bad census %d %v", len(ids), err)
				}
			} else if err == nil {
				t.Fatal("incomplete census accepted")
			}
		})
	}
}
func TestSyncInvalidationKeepsTokenOutOfURLs(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != "POST" || r.Header.Get("Authorization") != "Bearer fixture-token" || r.URL.RawQuery != "" {
			t.Error("invalidation request changed")
		}
		fmt.Fprint(w, `{"deleted":3}`)
	}))
	defer server.Close()
	if notifySyncTypeahead(context.Background(), server.Client(), server.URL, "") {
		t.Fatal("missing credential accepted")
	}
	if !notifySyncTypeahead(context.Background(), server.Client(), server.URL, "fixture-token") || calls != 1 {
		t.Fatal("invalidation failed")
	}
}
