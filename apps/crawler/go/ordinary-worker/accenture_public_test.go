package worker

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestAccentureOriginalPublicWorkerInventories(t *testing.T) {
	dir := os.Getenv("JOBSEEK_ACCENTURE_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original complete inventories and original normalized fields")
	}
	raw, err := os.ReadFile(filepath.Join(dir, "native1006-accenture-original-normalized-descriptions2-2026-10-10.json"))
	var oracle struct {
		Source       string `json:"original_source_revision"`
		Boards, Jobs int
		Records      []struct {
			Corpus, Board string
			SHA           string    `json:"corpus_sha256"`
			Descriptions  []*string `json:"normalized_descriptions"`
			Titles        []*string `json:"coerced_titles"`
			Jobs          int
		}
	}
	if err != nil || json.Unmarshal(raw, &oracle) != nil || oracle.Source != "17a3c03eb9e7c053d2b891195993d0d235ff8f54" || oracle.Boards != 12 || oracle.Jobs != 31752 || len(oracle.Records) != 12 {
		t.Fatal("original public field oracle unavailable")
	}
	for _, record := range oracle.Records {
		t.Run(filepath.Base(record.Corpus), func(t *testing.T) {
			if filepath.Base(record.Corpus) != record.Corpus {
				t.Fatal("capture path outside protected directory")
			}
			raw, err := os.ReadFile(filepath.Join(dir, record.Corpus))
			if err != nil || fmt.Sprintf("%x", sha256.Sum256(raw)) != record.SHA {
				t.Fatal("immutable original inventory changed")
			}
			var capture struct {
				Board    string
				Metadata map[string]any
				Pages    []struct {
					Body     string `json:"request_body"`
					Response json.RawMessage
				}
				Expected []map[string]any `json:"original_expected"`
			}
			if json.Unmarshal(raw, &capture) != nil || capture.Board != record.Board || len(capture.Expected) != record.Jobs || len(record.Descriptions) != record.Jobs || len(record.Titles) != record.Jobs || len(capture.Pages) == 0 {
				t.Fatal("complete original public snapshot unavailable")
			}
			md, _ := json.Marshal(capture.Metadata)
			profile := queue.GreenhouseMonitorProfile{Provider: "accenture", Profile: "accenture.http-items/v1", Endpoint: capture.Board}
			config := map[string]string{"crawler_type": "accenture", "board_url": capture.Board, "metadata": string(md)}
			listing, pages := 0, 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == "GET" {
					listing++
					if listing != 1 || pages != 0 || "https://"+r.Host+r.URL.String() != capture.Board {
						t.Error("public listing bootstrap scope changed")
					}
					fmt.Fprint(w, "<html>Public listing bootstrap</html>")
					return
				}
				if pages >= len(capture.Pages) || r.Method != "POST" || "https://"+r.Host+r.URL.String() != "https://www.accenture.com/api/accenture/elastic/findjobs" || r.Header.Get("Content-Type") != "multipart/form-data; boundary=----FormBoundary" {
					t.Error("original public API scope changed")
					w.WriteHeader(400)
					return
				}
				p := capture.Pages[pages]
				pages++
				body, err := io.ReadAll(r.Body)
				if err != nil || string(body) != p.Body {
					t.Error("original public multipart body changed")
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write(p.Response)
			}))
			result, err := discoverAccentureHTTPInventory(context.Background(), client.client, profile, config)
			if err != nil || result.Truncated || len(result.Jobs) != record.Jobs || listing != 1 || pages != len(capture.Pages) {
				t.Fatal("complete public native HTTP inventory changed", err)
			}
			for i, job := range result.Jobs {
				got := map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "date_posted": job.DatePosted, "metadata": job.Metadata, "job_location_type": job.JobLocationType}
				body, err := json.Marshal(got)
				var value map[string]any
				if err != nil || json.Unmarshal(body, &value) != nil {
					t.Fatal("native fields unavailable")
				}
				want := capture.Expected[i]
				want["title"] = nil
				if record.Titles[i] != nil {
					want["title"] = *record.Titles[i]
				}
				want["description"] = nil
				if record.Descriptions[i] != nil {
					want["description"] = *record.Descriptions[i]
				}
				for _, fields := range []map[string]any{value, want} {
					for key, v := range fields {
						if v == nil {
							delete(fields, key)
						}
					}
				}
				if !reflect.DeepEqual(value, want) {
					changed := []string{}
					for key, v := range want {
						if !reflect.DeepEqual(v, value[key]) {
							changed = append(changed, key)
							if key == "description" {
								got, _ := value[key].(string)
								wanted, _ := v.(string)
								at := 0
								for at < len(got) && at < len(wanted) && got[at] == wanted[at] {
									at++
								}
								t.Log("description bytes differ", len(got), len(wanted), "first difference", at)
							}
						}
					}
					t.Fatalf("original normalized worker fields changed at row %d: %v", i, changed)
				}
			}
			t.Log("complete original API requests and normalized worker fields match", record.Jobs)
		})
	}
}
