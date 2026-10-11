package main

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func healthyPreflight() map[string]float64 {
	return map[string]float64{"sampler_age_seconds": 30, "ready": 1, "data_free_ratio": .4, "repository_free_ratio": .6, "emergency_reserve_bytes": 2147483648, "backup_attempt_success": 1, "backup_age_seconds": 3600, "archive_failures_1h": 0}
}

func TestPreflightPreservesOriginalRecoveryThresholds(t *testing.T) {
	if err := validatePreflight(healthyPreflight()); err != nil {
		t.Fatal(err)
	}
	for field, value := range map[string]float64{"ready": 0, "data_free_ratio": .14, "repository_free_ratio": .19, "emergency_reserve_bytes": 0, "backup_attempt_success": 0, "backup_age_seconds": 129601, "archive_failures_1h": 1, "sampler_age_seconds": 301} {
		values := healthyPreflight()
		values[field] = value
		if validatePreflight(values) == nil {
			t.Fatal("unhealthy original contract accepted", field)
		}
	}
	for _, value := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		values := healthyPreflight()
		values["data_free_ratio"] = value
		if validatePreflight(values) == nil {
			t.Fatal("non-finite telemetry accepted")
		}
	}
}

func TestPreflightQueriesAuthenticatedOriginAndRefusesIncompleteEvidence(t *testing.T) {
	for _, mode := range []string{"healthy", "missing", "nonfinite", "redirect", "badjson", "multiscalar"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				username, password, ok := r.BasicAuth()
				if !ok || username != "fixture-user" || password != "fixture-password" || r.Method != "GET" || r.URL.Path != "/api/prom/api/v1/query" {
					t.Error("query scope/auth differs")
				}
				if calls >= len(preflightQueries) {
					t.Fatal("query budget exceeded")
				}
				query := preflightQueries[calls]
				calls++
				if r.URL.Query().Get("query") != query.query {
					t.Error("original query differs")
				}
				switch mode {
				case "redirect":
					w.Header().Set("Location", "/different")
					w.WriteHeader(302)
					return
				case "badjson":
					w.Write([]byte("invalid"))
					return
				}
				rows := []any{map[string]any{"value": []any{1234, healthyPreflight()[query.name]}}}
				switch mode {
				case "missing":
					rows = nil
				case "nonfinite":
					rows = []any{map[string]any{"value": []any{1234, "NaN"}}}
				case "multiscalar":
					rows = append(rows, rows[0])
				}
				json.NewEncoder(w).Encode(map[string]any{"status": "success", "data": map[string]any{"result": rows}})
			}))
			defer server.Close()
			err := postgresPreflight(context.Background(), server.URL+"/api/prom/push", "fixture-user", "fixture-password")
			if (err == nil) != (mode == "healthy") {
				t.Fatal("incomplete preflight outcome differs", mode, err)
			}
			if mode == "healthy" && calls != 8 || mode == "redirect" && calls != 1 {
				t.Fatal("query or redirect budget differs", calls)
			}
			if err != nil && strings.Contains(err.Error(), "fixture-password") {
				t.Fatal("credential entered diagnostic")
			}
		})
	}
}

func TestCSVManifestPreservesOriginalSortedExactBytes(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "nested"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "z.csv"), []byte("abc"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "nested/a.csv"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	body, err := csvManifest(root)
	if err != nil {
		t.Fatal(err)
	}
	expected := "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  nested/a.csv\nba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  z.csv\n"
	if string(body) != expected {
		t.Fatal("original manifest byte format/order differs")
	}
	manifest := filepath.Join(t.TempDir(), "data-files.sha256")
	if err := writeManifest(manifest, body); err != nil {
		t.Fatal(err)
	}
	if err := verifyManifest(manifest, body); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "z.csv"), []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	changed, err := csvManifest(root)
	if err != nil || verifyManifest(manifest, changed) == nil {
		t.Fatal("modified tree accepted")
	}
	if err := os.Symlink(manifest, filepath.Join(root, "evil.csv")); err != nil {
		t.Fatal(err)
	}
	if _, err := csvManifest(root); err == nil {
		t.Fatal("symlink tree accepted")
	}
}

func TestCSVManifestRejectsEmptyUnexpectedAndUnsafeOutputs(t *testing.T) {
	root := t.TempDir()
	if _, err := csvManifest(root); err == nil {
		t.Fatal("empty tree accepted")
	}
	if err := os.WriteFile(filepath.Join(root, "unexpected.txt"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := csvManifest(root); err == nil {
		t.Fatal("unexpected file accepted")
	}
	output := filepath.Join(t.TempDir(), "symlink.sha256")
	if err := os.Symlink(filepath.Join(root, "unexpected.txt"), output); err != nil {
		t.Fatal(err)
	}
	if writeManifest(output, []byte("overwrite")) == nil {
		t.Fatal("unsafe manifest output accepted")
	}
	if verifyManifest(output, nil) == nil {
		t.Fatal("unsafe manifest input accepted")
	}
}
