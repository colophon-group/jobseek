package queue

import (
	"context"
	"errors"
	"testing"
)

func TestRealPendingUnisanteRejectsIncompleteInventoryAuthority(t *testing.T) {
	for _, mode := range []string{"filtered", "truncated", "empty", "ordinary-chunk"} {
		t.Run(mode, func(t *testing.T) {
			p := firstProviderBatchFixture(t, "unisante")
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			cycle, err := a.BeginGreenhouseCycle(ctx, claim)
			if err != nil {
				t.Fatal(err)
			}
			batch := []GreenhouseRichPosting{{URL: "https://emploi.unisante.ch/index.php/offre/42-clinical-role", SourceIdentity: "unisante:emploi:42", Content: &GreenhouseRichContent{Fields: GreenhouseRichFields{Titles: []string{"Clinical role"}, Locales: []string{"fr"}}}}}
			summary := GreenhouseInventorySummary{Discovered: 1}
			if mode == "filtered" {
				summary.Discovered = 2
				summary.ProcessingFiltered = 1
			}
			if mode == "truncated" {
				summary.Truncated = true
			}
			if mode == "empty" {
				batch = nil
				summary.Discovered = 0
			}
			if mode == "ordinary-chunk" {
				_, err = cycle.WriteRichBatch(ctx, batch)
			} else {
				_, err = cycle.WriteCompleteUnisanteBatch(ctx, batch, summary)
			}
			if !errors.Is(err, ErrConfiguration) {
				t.Fatal("incomplete inventory granted migration write authority", err)
			}
			if _, err := cycle.FinishSuccess(ctx, summary); !errors.Is(err, ErrConfiguration) {
				t.Fatal("pending migration gained healthy success without complete adoption", err)
			}
			var identity string
			var receipt []byte
			var active bool
			if err := p.f.observer.QueryRow(ctx, "SELECT source_identity,is_active FROM job_posting WHERE id=$1::uuid", p.f.task.ID).Scan(&identity, &active); err != nil {
				t.Fatal(err)
			}
			if err := p.f.observer.QueryRow(ctx, "SELECT metadata->'_identity_migration_receipt' FROM job_board WHERE id=$1::uuid", p.f.task.ID).Scan(&receipt); err != nil {
				t.Fatal(err)
			}
			if identity != batchUnisanteLegacyURL || !active || len(receipt) != 0 {
				t.Fatal("rejected inventory changed legacy posting or receipt")
			}
		})
	}
}

const batchUnisanteLegacyURL = "https://emploi.unisante.ch/index.php/offre/42-clinical-role"

func TestRealLastHTTPMonitorColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"infor", "peoplesoft", "papa_johns", "papa_johns/proxy", "unisante"}, firstProviderBatchFixture)
}

func TestRealLastHTTPPairedDetailColdRetirement(t *testing.T) {
	for _, provider := range []string{"infor", "peoplesoft"} {
		t.Run(provider, func(t *testing.T) { testFirstAPIDetailRetirement(t, provider) })
	}
}
