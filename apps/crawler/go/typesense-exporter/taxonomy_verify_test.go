package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

type taxonomyFixture struct {
	Input     taxonomyInputs    `json:"input"`
	Documents taxonomyDocuments `json:"documents"`
	Cases     []struct {
		Name     string                    `json:"name"`
		Metadata map[string]map[string]any `json:"metadata"`
		Remote   taxonomyDocuments         `json:"remote"`
		Evidence map[string]any            `json:"evidence"`
	} `json:"cases"`
}

func readTaxonomyFixture(t *testing.T) taxonomyFixture {
	t.Helper()
	data, err := os.ReadFile("testdata/taxonomy_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var fixture taxonomyFixture
	if err := decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}
func TestTaxonomyFullEvidenceMatchesPython(t *testing.T) {
	fixture := readTaxonomyFixture(t)
	contract, err := loadTaxonomyContract()
	if err != nil {
		t.Fatal(err)
	}
	documents, err := buildTaxonomyDocuments(fixture.Input, contract)
	if err != nil {
		t.Fatal(err)
	}
	actual, err := taxonomyCanonicalJSON(documents, false)
	if err != nil {
		t.Fatal(err)
	}
	expected, err := taxonomyCanonicalJSON(fixture.Documents, false)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(actual, expected) {
		t.Fatalf("authoritative builders differ from Python\n%s\n%s", actual, expected)
	}
	for _, test := range fixture.Cases {
		t.Run(test.Name, func(t *testing.T) {
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Header.Get("X-TYPESENSE-API-KEY") != "fixture-key" {
					t.Error("unexpected request contract")
				}
				parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
				if len(parts) < 2 {
					http.NotFound(w, r)
					return
				}
				name := parts[1]
				if len(parts) == 2 {
					json.NewEncoder(w).Encode(test.Metadata[name])
					return
				}
				query := r.URL.Query()
				page, _ := strconv.Atoi(query.Get("page"))
				size, _ := strconv.Atoi(query.Get("per_page"))
				if size != 3 || query.Get("q") != "*" {
					t.Error("unbounded taxonomy search")
				}
				docs := test.Remote[name]
				hits := []map[string]any{}
				for i := (page - 1) * size; i < len(docs) && i < page*size; i++ {
					hits = append(hits, map[string]any{"document": docs[i]})
				}
				json.NewEncoder(w).Encode(map[string]any{"found": len(docs), "hits": hits})
			}))
			defer server.Close()
			// The HTTP encoder must preserve integral floats like the Python server.
			// encode Go map values containing json.Number to retain remote JSON types.
			evidence, err := verifyTaxonomySnapshot(context.Background(), taxonomyReader{Client: server.Client(), BaseURL: server.URL, Key: "fixture-key"}, contract, documents, 3)
			if err != nil {
				t.Fatal(err)
			}
			actual, err := taxonomyCanonicalJSON(evidence, false)
			if err != nil {
				t.Fatal(err)
			}
			expected, err := taxonomyCanonicalJSON(test.Evidence, false)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(actual, expected) {
				t.Fatalf("evidence differs from Python\nGo: %s\nPython: %s", actual, expected)
			}
			if calls != evidence["typesense_calls"].(map[string]any)["total"] {
				t.Fatal("request accounting differs")
			}
		})
	}
}
func TestTaxonomyHierarchyAndDisplayFailures(t *testing.T) {
	contract, _ := loadTaxonomyContract()
	for _, kind := range []string{"cycle", "missing_parent", "conflicting_name", "blank_slug", "missing_english", "conflicting_occupation", "conflicting_industry"} {
		t.Run(kind, func(t *testing.T) {
			fixture := readTaxonomyFixture(t)
			input := fixture.Input
			switch kind {
			case "cycle":
				input.Locations[0].Parent = &input.Locations[0].ID
			case "missing_parent":
				id := 999
				input.Locations[0].Parent = &id
			case "conflicting_name":
				row := input.LocationNames[0]
				row.Name = "conflict"
				input.LocationNames = append(input.LocationNames, row)
			case "blank_slug":
				input.Technologies[0].Slug = " \t"
			case "missing_english":
				input.LocationNames = nil
			case "conflicting_occupation":
				row := input.Occupations[0]
				row.Name = "conflict"
				input.Occupations = append(input.Occupations, row)
			case "conflicting_industry":
				row := input.IndustryNames[0]
				row.Name = "conflict"
				input.IndustryNames = append(input.IndustryNames, row)
			}
			if _, err := buildTaxonomyDocuments(input, contract); err == nil {
				t.Fatal("invalid authority accepted")
			}
		})
	}
}
func TestTaxonomyPaginationFailsClosed(t *testing.T) {
	contract, _ := loadTaxonomyContract()
	spec := contract.Collections[0]
	fixture := readTaxonomyFixture(t)
	documents := fixture.Documents["location"]
	for _, kind := range []string{"count_drift", "early_end", "repeat", "invalid_count", "invalid_hit", "unsafe_id", "duplicate_authority"} {
		t.Run(kind, func(t *testing.T) {
			expected := documents
			if kind == "duplicate_authority" {
				expected = append(append([]map[string]any{}, documents...), documents[0])
			}
			calls := 0
			search := func(_ context.Context, _ string, _ url.Values) (map[string]any, error) {
				calls++
				found := any(json.Number("10"))
				hits := []any{map[string]any{"document": documents[0]}}
				switch kind {
				case "count_drift":
					if calls > 1 {
						found = json.Number("11")
					}
				case "early_end":
					if calls > 1 {
						hits = []any{}
					}
				case "invalid_count":
					found = true
				case "invalid_hit":
					hits = []any{nil}
				case "unsafe_id":
					hits = []any{map[string]any{"document": map[string]any{"id": "unsafe/id"}}}
				}
				return map[string]any{"found": found, "hits": hits}, nil
			}
			_, _, err := compareTaxonomyCollection(context.Background(), search, spec, expected, map[string]any{"num_documents": json.Number("10")}, 3)
			if err == nil {
				t.Fatal("unverifiable pagination accepted")
			}
			if calls > 2 {
				t.Fatal("pagination did not terminate promptly")
			}
		})
	}
}
func TestTaxonomyPresenceNumericAndHashSemantics(t *testing.T) {
	if !taxonomyEqual(json.Number("1.0"), 1) || taxonomyEqual(json.Number("9007199254740993"), json.Number("9007199254740993.0")) {
		t.Fatal("numeric comparison lost Python precision semantics")
	}
	contract, _ := loadTaxonomyContract()
	spec := contract.Collections[4]
	spec.Minimum = 1
	wanted := []map[string]any{{"id": "one"}}
	search := func(context.Context, string, url.Values) (map[string]any, error) {
		return map[string]any{"found": json.Number("1"), "hits": []any{map[string]any{"document": map[string]any{"id": "one", "industry_id": nil}}}}, nil
	}
	evidence, _, err := compareTaxonomyCollection(context.Background(), search, spec, wanted, map[string]any{"num_documents": json.Number("1")}, 250)
	if err != nil {
		t.Fatal(err)
	}
	details := evidence["mismatch_details"].([]map[string]any)
	if evidence["status"] != "not_ready" || !reflect.DeepEqual(details[0]["fields"], []string{"industry_id"}) {
		t.Fatal("null and absence collapsed")
	}
}
func TestTaxonomyTransportRejectsMalformedData(t *testing.T) {
	for _, body := range []string{"null", "{} {}", `{"found":1,"hits":[]}` + string([]byte{0xff})} {
		t.Run(body, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(body)) }))
			defer server.Close()
			if _, err := (taxonomyReader{Client: server.Client(), BaseURL: server.URL, Key: "fixture"}).get(context.Background(), "company", nil); err == nil {
				t.Fatal("malformed response accepted")
			}
		})
	}
}
