package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// Representative of the 2,570-member production cohort, where decoding every
// operation amplified fleet growth into recurring allocation and CPU work.
func BenchmarkOwnershipFleetDecode(b *testing.B) {
	doc := ownershipDocument{Version: ownershipVersion, Epoch: 151, SourceRevision: strings.Repeat("a", 40)}
	for i := 0; i < 2570; i++ {
		id := fmt.Sprintf("10000000-0000-0000-0000-%012x", i)
		config := profileConfig()
		profile, err := InspectGreenhouseMonitor(id, config)
		if err != nil {
			b.Fatal(err)
		}
		doc.Members = append(doc.Members, ownershipMember{id, profile.CompanyID, profile.Domain, Monitor, Simple, greenhouseOwnershipProfile, profile.EffectiveConfigSHA256, config})
	}
	body, err := json.Marshal(doc)
	if err != nil {
		b.Fatal(err)
	}
	sum := sha256.Sum256(body)
	digest := hex.EncodeToString(sum[:])
	b.ReportAllocs()
	b.ResetTimer()
	for b.Loop() {
		if _, err := decodeOwnership(string(body), digest); err != nil {
			b.Fatal(err)
		}
	}
}
