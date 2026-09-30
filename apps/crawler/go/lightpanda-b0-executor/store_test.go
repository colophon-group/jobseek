package executor

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgconn"
)

func TestSQLMatchesFrozenPythonAuthoritativeQueries(t *testing.T) {
	data, err := os.ReadFile("testdata/python_queries.json")
	if err != nil {
		t.Fatal(err)
	}
	var expected map[string]struct{ SHA256 string }
	if err := json.Unmarshal(data, &expected); err != nil {
		t.Fatal(err)
	}
	queries := map[string]string{"updateContentSQL": updateContentSQL, "recordSuccessSQL": recordSuccessSQL, "recordFailureSQL": recordFailureSQL, "recordTransientSQL": recordTransientSQL, "upsertDescriptionSQL": upsertDescriptionSQL, "currentDetailSQL": currentDetailSQL, "fetchPostingForEnrichSQL": fetchPostingForEnrichSQL}
	if len(queries) != len(expected) {
		t.Fatal("query coverage changed")
	}
	for name, query := range queries {
		digest := sha256.Sum256([]byte(query))
		if hex.EncodeToString(digest[:]) != expected[name].SHA256 {
			t.Fatalf("%s differs from Python SQL", name)
		}
	}
}

func TestFrozenPythonDescriptionByteHashes(t *testing.T) {
	data, err := os.ReadFile("testdata/python_description_hashes.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []struct {
		HTML, Language, Locale string
		Present                bool
		Hash                   int64
	}
	if err := json.Unmarshal(data, &rows); err != nil {
		t.Fatal(err)
	}
	for i, row := range rows {
		value, err := StageDescription(row.HTML, row.Language)
		if err != nil {
			t.Fatal(err)
		}
		if (value != nil) != row.Present {
			t.Fatalf("case %d empty-content staging differs", i)
		}
		if value != nil && (value.HTML != row.HTML || value.Locale != row.Locale || value.Hash != row.Hash) {
			t.Fatalf("case %d Python byte hash/locale differs", i)
		}
	}
	if _, err := StageDescription(string([]byte{255}), "en"); err == nil {
		t.Fatal("invalid UTF-8 accepted")
	}
}

func TestFenceRejectsMalformedIdentityBeforeDatabaseIO(t *testing.T) {
	f := Fence{PostingID: "11111111-1111-1111-1111-111111111111", ShardID: "lightpanda-b0", RoutingEpoch: 137, ConfigRevision: 1, PayloadSHA256: strings.Repeat("a", 64), ClaimToken: "137:1"}
	if err := f.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*Fence){
		func(f *Fence) { f.ClaimToken = "136:1" }, func(f *Fence) { f.ClaimToken = "137:0" }, func(f *Fence) { f.ClaimToken = "137:01" },
		func(f *Fence) { f.PostingID = "{11111111-1111-1111-1111-111111111111}" }, func(f *Fence) { f.PayloadSHA256 = strings.Repeat("A", 64) },
		func(f *Fence) { f.RoutingEpoch = 0 }, func(f *Fence) { f.ConfigRevision = maxIdentityInteger + 1 }, func(f *Fence) { f.ShardID = "../other" },
	} {
		invalid := f
		change(&invalid)
		if err := invalid.Validate(); err == nil {
			t.Fatal("invalid claim identity accepted")
		}
	}
}

func TestOnlyExactDatabaseFenceRejectionBecomesAuthorityLost(t *testing.T) {
	known := fenceError(&pgconn.PgError{Code: "P0001", Message: "lightpanda_b0_write_fence_rejected", Detail: "routing_epoch_not_current"})
	if !errors.Is(known, ErrAuthorityLost) || !strings.Contains(known.Error(), "routing_epoch_not_current") {
		t.Fatal("known authority loss not preserved")
	}
	untrusted := fenceError(&pgconn.PgError{Code: "P0001", Message: "lightpanda_b0_write_fence_rejected", Detail: "private source data"})
	if !errors.Is(untrusted, ErrAuthorityLost) || strings.Contains(untrusted.Error(), "private source data") {
		t.Fatal("unreviewed error detail crossed boundary")
	}
	other := &pgconn.PgError{Code: "P0001", Message: "different database failure"}
	if fenceError(other) != other || errors.Is(fenceError(other), ErrAuthorityLost) {
		t.Fatal("ordinary database failure became authority loss")
	}
}
