package main

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
)

func TestB0CutoverProducerMatchesActualOriginalDecisions(t *testing.T) {
	body, err := os.ReadFile("testdata/python_b0_cutover_producer.json")
	var corpus struct {
		Vectors []struct {
			Name     string
			Payload  string `json:"payload_hex"`
			Accepted bool
			Decision struct {
				Outcome      string
				Prep         string `json:"preparation_digest"`
				Payload      string `json:"payload_sha256"`
				Existing     string `json:"existing_state"`
				ExistingHash string `json:"existing_payload_sha256"`
				Activated    bool
				Cohort       string
				Slugs        []string `json:"board_slugs"`
				Occupancy    int64    `json:"lifetime_occupancy"`
				Capacity     int64    `json:"lifetime_capacity"`
				Headroom     int64    `json:"lifetime_headroom"`
			}
		}
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Vectors) < 50 {
		t.Fatal("original producer decision corpus unavailable")
	}
	for _, c := range corpus.Vectors {
		t.Run(c.Name, func(t *testing.T) {
			raw, err := hex.DecodeString(c.Payload)
			if err != nil {
				t.Fatal(err)
			}
			d, err := decodeB0CutoverProducer(raw)
			if (err == nil) != c.Accepted {
				t.Fatal("original acceptance differs", err)
			}
			if err == nil && (d.Outcome != c.Decision.Outcome || d.PreparationDigest != c.Decision.Prep || d.PayloadSHA256 != c.Decision.Payload || d.ExistingState != c.Decision.Existing || d.ExistingPayloadSHA256 != c.Decision.ExistingHash || d.Activated != c.Decision.Activated || d.Cohort != c.Decision.Cohort || len(d.BoardSlugs) != len(c.Decision.Slugs) || d.LifetimeOccupancy != c.Decision.Occupancy || d.LifetimeCapacity != c.Decision.Capacity || d.LifetimeHeadroom != c.Decision.Headroom) {
				t.Fatal("original complete decision fields differ")
			}
			for i, slug := range c.Decision.Slugs {
				if err == nil && d.BoardSlugs[i] != slug {
					t.Fatal("manifest board identity differs")
				}
			}
		})
	}
}
