package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
)

// Called from the PostgreSQL snapshot fixture after its real authority is
// populated. The normal path has no job_posting table: all posting-derived
// counts must come from the same Typesense facets used by the web/refresh.
func testSyncRuntimeFixture(t *testing.T, dsn, schema string) {
	t.Helper()
	var mutex sync.Mutex
	docs := map[string]map[string]map[string]any{}
	upserts, updates, facets, censuses := 0, 0, 0, 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mutex.Lock()
		defer mutex.Unlock()
		if r.Header.Get("X-TYPESENSE-API-KEY") != "sync-fixture" {
			t.Error("missing scoped key")
		}
		parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
		if len(parts) < 2 {
			http.NotFound(w, r)
			return
		}
		collection := parts[1]
		if strings.HasSuffix(r.URL.Path, "/import") {
			action := r.URL.Query().Get("action")
			if action == "upsert" {
				upserts++
			} else if action == "update" {
				updates++
			} else {
				t.Error("unsupported import action")
			}
			if docs[collection] == nil {
				docs[collection] = map[string]map[string]any{}
			}
			scanner := bufio.NewScanner(r.Body)
			for scanner.Scan() {
				var doc map[string]any
				if err := json.Unmarshal(scanner.Bytes(), &doc); err != nil {
					t.Error(err)
					return
				}
				id := doc["id"].(string)
				if action == "upsert" {
					docs[collection][id] = doc
				} else {
					for key, value := range doc {
						docs[collection][id][key] = value
					}
				}
				fmt.Fprintln(w, `{"success":true}`)
			}
			return
		}
		if collection == "job_posting" {
			facets++
			fmt.Fprint(w, `{"facet_counts":[]}`)
			return
		}
		if collection == "company" && len(parts) == 2 {
			censuses++
			json.NewEncoder(w).Encode(map[string]any{"num_documents": len(docs[collection])})
			return
		}
		if collection == "company" && strings.HasSuffix(r.URL.Path, "/search") {
			hits := []map[string]any{}
			for id := range docs[collection] {
				hits = append(hits, map[string]any{"document": map[string]any{"id": id}})
			}
			json.NewEncoder(w).Encode(map[string]any{"found": len(hits), "hits": hits})
			return
		}
		t.Error("unexpected sync endpoint: " + r.Method + " " + r.URL.Path)
		http.NotFound(w, r)
	}))
	defer server.Close()
	databaseURL, err := url.Parse(dsn)
	if err != nil {
		t.Fatal(err)
	}
	query := databaseURL.Query()
	query.Set("search_path", schema)
	databaseURL.RawQuery = query.Encode()
	endpoint, _ := url.Parse(server.URL)
	host, port, err := net.SplitHostPort(endpoint.Host)
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("LOCAL_DATABASE_URL", databaseURL.String())
	t.Setenv("TYPESENSE_HOST", host)
	t.Setenv("TYPESENSE_PORT", port)
	t.Setenv("TYPESENSE_PROTOCOL", "http")
	t.Setenv("TYPESENSE_OPERATIONS_KEY", "sync-fixture")
	t.Setenv("WEB_INVALIDATE_URL", "")
	t.Setenv("INTERNAL_REVALIDATE_TOKEN", "")
	if err := runSyncTaxonomies(); err != nil {
		t.Fatal(err)
	}
	mutex.Lock()
	defer mutex.Unlock()
	if upserts != 5 || updates != 5 || facets != 12 || censuses != 2 {
		t.Fatalf("incomplete runtime: upsert=%d update=%d facets=%d census=%d", upserts, updates, facets, censuses)
	}
	company := docs["company"]["00000000-0000-0000-0000-000000000001"]
	if company["description_fr"] != "<p>Bonjour</p>" || company["active_posting_count"] != float64(0) {
		t.Fatal("runtime company payload changed")
	}
}
