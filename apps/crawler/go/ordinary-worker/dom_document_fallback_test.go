package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"reflect"
	"testing"
)

type domDocumentReference struct {
	Name, Body, Source, Mode string
	Config                   map[string]any
	Output                   map[string]any
	Error                    bool
}

func domDocumentReferences(t *testing.T) []domDocumentReference {
	t.Helper()
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_dom_document_fallback.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []domDocumentReference
	if json.Unmarshal(raw, &rows) != nil || len(rows) != 30 {
		t.Fatal("original document corpus invalid")
	}
	return rows
}
func TestDOMDocumentFallbackOriginalHTTPAndBinaryParity(t *testing.T) {
	for _, row := range domDocumentReferences(t) {
		t.Run(row.Name, func(t *testing.T) {
			body, err := base64.StdEncoding.DecodeString(row.Body)
			if err != nil {
				t.Fatal(err)
			}
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if row.Mode == "header-reserved" {
					w.Header().Set("TDM-Reservation", "1")
				}
				if row.Mode == "http-error" {
					w.WriteHeader(403)
				}
				w.Write(body)
			})
			got, err := dom.FetchDetailWithDocumentParser(context.Background(), row.Source, row.Config, client.client, parseDOMBinaryDocument)
			if row.Mode == "header-reserved" {
				if err == nil || got.ErrorKind != "tdm" || len(got.Content) != 0 {
					t.Fatal("publisher header was not applied before binary parsing")
				}
				return
			}
			expected := map[string]any{}
			for k, v := range row.Output {
				if v != nil {
					expected[k] = v
				}
			}
			if row.Error || len(expected) == 0 {
				if err == nil && len(got.Content) > 0 {
					t.Fatal("failed document produced content")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			raw, _ := json.Marshal(got.Content)
			var content map[string]any
			json.Unmarshal(raw, &content)
			for k, v := range content {
				if v == nil {
					delete(content, k)
				}
			}
			if !reflect.DeepEqual(content, expected) {
				t.Fatalf("original content differs: got=%v want=%v", content, expected)
			}
		})
	}
}
func TestRealDOMDocumentFallbackCanonicalWritesAndPublisherConservation(t *testing.T) {
	for _, row := range domDocumentReferences(t) {
		if row.Name != "pdf-success" && row.Name != "docx-success" && row.Name != "doc-text-success" && row.Name != "pdf-header-reserved" && row.Name != "docx-header-reserved" && row.Name != "invalid-doc-success" {
			continue
		}
		t.Run(row.Name, func(t *testing.T) {
			raw, _ := json.Marshal(map[string]any{"scraper_type": "dom", "scraper_config": row.Config})
			f, a, claim := independentDetailOwnedFixture(t, string(raw), "")
			ctx := context.Background()
			body, _ := base64.StdEncoding.DecodeString(row.Body)
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if row.Mode == "header-reserved" {
					w.Header().Set("TDM-Reservation", "1")
				}
				w.Write(body)
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("binary document did not settle", result, err)
			}
			var reserved bool
			var descriptions int
			if err = f.pg.QueryRow(ctx, `SELECT tdm_reserved,(SELECT count(*) FROM descriptions WHERE posting_id=$1::uuid) FROM job_posting WHERE id=$1::uuid`, f.original).Scan(&reserved, &descriptions); err != nil {
				t.Fatal(err)
			}
			if row.Mode == "header-reserved" {
				if !reserved || descriptions != 0 {
					t.Fatal("publisher conservation lost")
				}
			} else if row.Name == "invalid-doc-success" {
				if descriptions != 0 {
					t.Fatal("invalid binary wrote description")
				}
			} else if result.Cycle.Status != "succeeded" || descriptions != 1 {
				t.Fatal("canonical binary write failed", result.Cycle.Status, descriptions)
			}
			if f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("binary attempt leaked lease")
			}
		})
	}
}
