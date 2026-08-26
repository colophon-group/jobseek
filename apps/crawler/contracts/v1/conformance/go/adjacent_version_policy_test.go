package adjacentpolicy

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

type endpoint struct {
	Package string `json:"package"`
	Version int    `json:"version"`
}

type manifestShape struct {
	From endpoint `json:"from"`
	To   endpoint `json:"to"`
}

type lossShape struct {
	Path   string `json:"path"`
	Reason string `json:"reason"`
}

type expectedShape struct {
	Losses  []lossShape    `json:"losses"`
	Payload map[string]any `json:"payload"`
}

type vectorCase struct {
	Direction  string         `json:"direction"`
	Expected   expectedShape  `json:"expected"`
	ID         string         `json:"id"`
	Input      map[string]any `json:"input"`
	Reversible bool           `json:"reversible"`
}

type vectorDocument struct {
	Cases []vectorCase `json:"cases"`
}

func contractRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate adjacent-version Go test")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", ".."))
}

func policyRoot(t *testing.T) string {
	t.Helper()
	return filepath.Join(
		contractRoot(t),
		"fixtures",
		"compatibility",
		"adjacent_version_policy",
	)
}

func readJSONUseNumber(t *testing.T, path string, target any) []byte {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(target); err != nil {
		t.Fatal(err)
	}
	return content
}

func TestAdjacentVersionManifestIsStrictlyTestOnly(t *testing.T) {
	path := filepath.Join(policyRoot(t), "manifest.json")
	var raw map[string]json.RawMessage
	content := readJSONUseNumber(t, path, &raw)
	if string(raw["production"]) != "false" {
		t.Fatalf("production must be the exact JSON boolean false: %s", raw["production"])
	}
	if string(raw["fixture_only"]) != "true" {
		t.Fatalf("fixture_only must be the exact JSON boolean true: %s", raw["fixture_only"])
	}
	var manifest manifestShape
	decoder := json.NewDecoder(bytes.NewReader(content))
	if err := decoder.Decode(&manifest); err != nil {
		t.Fatal(err)
	}
	for _, item := range []endpoint{manifest.From, manifest.To} {
		if !strings.Contains(item.Package, ".runtime.policytest.v") {
			t.Fatalf("package is not unmistakably test-only: %s", item.Package)
		}
	}
	if manifest.To.Version != manifest.From.Version+1 {
		t.Fatalf("specimen versions are not adjacent: %d -> %d", manifest.From.Version, manifest.To.Version)
	}
}

func TestSharedVectorsKeepLargeIntegersPresenceAndRealLoss(t *testing.T) {
	var vectors vectorDocument
	readJSONUseNumber(t, filepath.Join(policyRoot(t), "vectors.json"), &vectors)
	if len(vectors.Cases) == 0 {
		t.Fatal("shared vector corpus is empty")
	}
	directions := map[string]bool{}
	lossy := map[string]bool{}
	reversible := map[string]bool{}
	sawLarge := false
	sawAbsent := false
	sawExplicitDefaults := false
	for _, item := range vectors.Cases {
		directions[item.Direction] = true
		if item.Reversible {
			reversible[item.Direction] = true
		} else {
			lossy[item.Direction] = true
			if len(item.Expected.Losses) == 0 || item.Expected.Payload == nil {
				t.Fatalf("lossy case lacks evidence: %s", item.ID)
			}
		}
		if number, ok := item.Input["sequence"].(json.Number); ok {
			if number.String() == "9007199254740993" || number.String() == "9007199254740997" {
				sawLarge = true
			}
		}
		_, countPresent := item.Input["explicit_count"]
		_, labelPresent := item.Input["explicit_label"]
		if !countPresent && !labelPresent {
			sawAbsent = true
		}
		if count, ok := item.Input["explicit_count"].(json.Number); ok && count.String() == "0" {
			if label, ok := item.Input["explicit_label"].(string); ok && label == "" {
				sawExplicitDefaults = true
			}
		}
	}
	for _, direction := range []string{"old_to_new", "new_to_old"} {
		if !directions[direction] || !lossy[direction] || !reversible[direction] {
			t.Fatalf("direction lacks reversible and lossy evidence: %s", direction)
		}
	}
	if !sawLarge || !sawAbsent || !sawExplicitDefaults {
		t.Fatal("vectors do not prove exact >2^53 and absence/default handling")
	}
}

func TestAdjacentVersionPythonAndGoConvertersExecuteOffline(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal("python3 is required for the cross-language policy gate")
	}
	root := contractRoot(t)
	checker := filepath.Join(root, "tools", "check_compatibility.py")
	command := exec.Command( // #nosec G204 -- executable and arguments are repository-owned.
		python,
		checker,
		"--root",
		root,
		"--adjacent-policy-only",
	)
	command.Env = append(
		os.Environ(),
		"GO111MODULE=off",
		"GOENV=off",
		"GOPROXY=off",
		"GOSUMDB=off",
		"GOTOOLCHAIN=local",
	)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("offline adjacent-version policy failed: %v\n%s", err, output)
	}
	if string(output) != "runtime v1 adjacent-version policy: ok\n" {
		t.Fatalf("unexpected adjacent-version policy output: %q", output)
	}
}
