package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

func TestInlineInventoryMatchesActualPythonMonitor(t *testing.T) {
	body, err := os.ReadFile("testdata/python_inline_inventory.json")
	var corpus struct {
		Cases []struct {
			Name, HTML, Now     string
			BoardURL            string `json:"board_url"`
			Metadata            json.RawMessage
			Error, Truncated    bool
			Jobs                []any
			VerifiedEmptyReason string `json:"verified_empty_reason"`
		}
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) < 40 {
		t.Fatal("actual Inline inventory corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			md, err := DecodeInlineMetadata(string(c.Metadata))
			if err != nil {
				t.Fatal("invalid frozen configuration")
			}
			options, err := InlineDocumentOptions(md)
			now, parseErr := time.Parse(time.RFC3339Nano, c.Now)
			if parseErr != nil {
				t.Fatal(parseErr)
			}
			got := InlineInventory{}
			if err == nil {
				got, err = ParseInlineDocument(context.Background(), c.HTML, c.BoardURL, options, now)
			}
			if (err != nil) != c.Error {
				t.Fatal("inline error contract differs", err)
			}
			if err != nil {
				return
			}
			encoded, _ := json.Marshal(got.Jobs)
			var jobs []any
			_ = json.Unmarshal(encoded, &jobs)
			// The shared Job representation omits the absent source identity field.
			if !reflect.DeepEqual(jobs, c.Jobs) || got.Truncated != c.Truncated || got.VerifiedEmptyReason != c.VerifiedEmptyReason {
				t.Fatalf("inline inventory differs: jobs=%v expected=%v truncated=%v/%v expiry=%q/%q", jobs, c.Jobs, got.Truncated, c.Truncated, got.VerifiedEmptyReason, c.VerifiedEmptyReason)
			}
		})
	}
}
