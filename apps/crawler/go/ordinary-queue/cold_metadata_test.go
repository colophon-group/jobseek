package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
)

func TestColdMetadataExactCrossRuntimeCorpus(t *testing.T) {
	body, err := os.ReadFile("testdata/cold_metadata.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Cases   []struct{ Raw, Canonical, SHA256 string }
		Invalid []string
	}
	if json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 15 || len(corpus.Invalid) != 12 {
		t.Fatal("shared numeric corpus missing")
	}
	for i, fixture := range corpus.Cases {
		actual, err := coldMetadata(fixture.Raw)
		hash := sha256.Sum256([]byte(actual))
		if err != nil || actual != fixture.Canonical || hex.EncodeToString(hash[:]) != fixture.SHA256 {
			t.Fatalf("exact cross-runtime metadata failed case %d", i)
		}
	}
	for i, raw := range corpus.Invalid {
		if _, err := coldMetadata(raw); err == nil {
			t.Fatalf("invalid metadata accepted case %d", i)
		}
	}
}
