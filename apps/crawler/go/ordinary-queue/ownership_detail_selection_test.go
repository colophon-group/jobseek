package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func fleetDetailSelectionFixture() *OwnershipPlan {
	p := &OwnershipPlan{}
	for i := 0; i < 7840; i++ {
		worker := Simple
		if i%4 == 0 {
			worker = Browser
		}
		p.document.Details = append(p.document.Details, ownershipDetail{BoardID: fmt.Sprintf("10000000-0000-0000-0000-%012x", i), Worker: worker, Profile: "fixture", Domain: "fixture.example"})
	}
	return p.indexDetailSelections()
}
func TestImmutableDetailSelectionAvoidsFleetAllocation(t *testing.T) {
	p := fleetDetailSelectionFixture()
	for _, worker := range []WorkerType{Simple, Browser} {
		details, bindings := p.detailSelection(worker)
		expected := 0
		for _, d := range p.document.Details {
			if d.Worker == worker {
				if expected >= len(details) || details[expected].BoardID != d.BoardID || bindings[d.BoardID].Worker != worker {
					t.Fatal("immutable selection reordered or crossed worker lanes")
				}
				expected++
			} else if _, ok := bindings[d.BoardID]; ok {
				t.Fatal("foreign worker admitted")
			}
		}
		if len(details) != expected || len(bindings) != expected {
			t.Fatal("immutable cohort lost bindings")
		}
		if allocated := testing.AllocsPerRun(20, func() { p.detailSelection(worker) }); allocated != 0 {
			t.Fatalf("immutable detail poll rebuilt fleet: %.0f allocations", allocated)
		}
	}
}
func BenchmarkImmutableDetailSelectionFleet(b *testing.B) {
	p := fleetDetailSelectionFixture()
	p.detailSelection(Simple)
	b.ReportAllocs()
	b.ResetTimer()
	for b.Loop() {
		details, bindings := p.detailSelection(Simple)
		if len(details) != 5880 || len(bindings) != 5880 {
			b.Fatal("fleet changed")
		}
	}
}

func TestDecodedOwnershipIndexesDetailsWithoutChangingDurableIdentity(t *testing.T) {
	c := profileConfig()
	c["metadata"] = `{"token":"fixture","scraper_type":"skip"}`
	id := "10000000-0000-4000-8000-000000000001"
	m, err := InspectRichMonitor(id, c)
	if err != nil {
		t.Fatal(err)
	}
	d, err := inspectDetailOwnership(id, c)
	if err != nil {
		t.Fatal(err)
	}
	doc := ownershipDocument{Version: ownershipVersion, ProjectionVersion: ownershipProjectionVersion, Epoch: 1, SourceRevision: strings.Repeat("a", 40), Members: []ownershipMember{{BoardID: id, CompanyID: m.CompanyID, Domain: m.Domain, Kind: Monitor, Worker: MonitorWorker(m), Profile: m.Profile, EffectiveConfigHash: m.EffectiveConfigSHA256, Config: c}}, Details: []ownershipDetail{{BoardID: id, CompanyID: d.CompanyID, Domain: d.Domain, Worker: Simple, Profile: d.Profile, EffectiveConfigHash: d.EffectiveBoardSHA256, Config: c}}}
	body, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(body)
	p, err := decodeOwnership(string(body), hex.EncodeToString(hash[:]))
	if err != nil {
		t.Fatal(err)
	}
	before, projection, projectionHash := p.body, p.projection, p.projectionHash
	if p.detailSelections == nil {
		t.Fatal("decoded runtime plan lacks immutable index")
	}
	if allocated := testing.AllocsPerRun(20, func() {
		details, bindings := p.detailSelection(Simple)
		if len(details) != 1 || len(bindings) != 1 {
			t.Fatal("decoded plan lost membership")
		}
	}); allocated != 0 {
		t.Fatal("decoded selection allocates", allocated)
	}
	if p.body != before || p.projection != projection || p.projectionHash != projectionHash {
		t.Fatal("selection changed durable plan identity")
	}
}
