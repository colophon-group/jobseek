package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Run the unchanged Python helper in the delivered wrapper as the oracle.
// This test dependency is offline; production Go helpers never launch Python.
func originalB0Reply(t *testing.T, function string, args []string) (string, bool) {
	t.Helper()
	body, err := os.ReadFile("testdata/b0_host_helpers_source1039.json")
	if err != nil {
		t.Fatal(err)
	}
	var oracle struct {
		Source  string            `json:"source_revision"`
		Helpers map[string]string `json:"helpers"`
	}
	if json.Unmarshal(body, &oracle) != nil || oracle.Source != "a5ecb5e9da5057cf8b6348f6cb203628347b5fc7" || len(oracle.Helpers) != 4 || oracle.Helpers[function] == "" {
		t.Fatal("delivered B0 host helper oracle differs")
	}
	command := exec.Command("python3", append([]string{"-"}, args...)...)
	command.Stdin = strings.NewReader(oracle.Helpers[function])
	output, err := command.Output()
	return strings.TrimSuffix(string(output), "\n"), err == nil
}

func TestB0ReceiptRepliesMatchDeliveredPythonHelpers(t *testing.T) {
	for _, state := range []string{"active", "pending", "rollback-pending", "rollback-cleared"} {
		for _, corruption := range []string{"none", "duplicate", "unknown", "missing", "nonascii", "crlf", "nul", "no-newline", "epoch-zero", "epoch-too-large", "bad-plan", "bad-compose", "bad-activated", "old-retirement", "unreserved-retirement", "bad-source-state", "bad-source-sha", "unsafe-mode", "symlink", "hardlink", "foreign-cohort", "foreign-image", "foreign-revision"} {
			t.Run(state+"/"+corruption, func(t *testing.T) {
				root := t.TempDir()
				path := filepath.Join(root, "receipt")
				image, revision, digest := "ghcr.io/example/jobseek-crawler@sha256:"+strings.Repeat("b", 64), strings.Repeat("c", 40), strings.Repeat("d", 64)
				body := "schema=jobseek.lightpanda-b0-active/v1\nstate=" + state + "\ncohort=cdom\nnamespace=production-b0\nshard_id=lightpanda-b0\nrouting_epoch=307\nplan_digest=" + digest + "\ncompose_digest=" + digest + "\ncrawler_image_ref=" + image + "\ndeploy_revision=" + revision + "\nactivated_at_epoch=1791919000\n"
				if strings.HasPrefix(state, "rollback-") {
					body += "source_receipt_state=active\nsource_receipt_sha256=" + strings.Repeat("e", 64) + "\nretirement_routing_epoch=308\n"
				}
				switch corruption {
				case "duplicate":
					body += "state=" + state + "\n"
				case "unknown":
					body += "unknown=value\n"
				case "missing":
					body = strings.ReplaceAll(body, "schema=jobseek.lightpanda-b0-active/v1\n", "")
				case "nonascii":
					body = strings.ReplaceAll(body, "cdom", "cdöm")
				case "crlf":
					body = strings.ReplaceAll(body, "\n", "\r\n")
				case "nul":
					body += "\x00"
				case "no-newline":
					body = strings.TrimSuffix(body, "\n")
				case "epoch-zero":
					body = strings.ReplaceAll(body, "routing_epoch=307", "routing_epoch=0")
				case "epoch-too-large":
					body = strings.ReplaceAll(body, "routing_epoch=307", "routing_epoch=10000000000000")
				case "bad-plan":
					body = strings.ReplaceAll(body, "plan_digest="+digest, "plan_digest=bad")
				case "bad-compose":
					body = strings.ReplaceAll(body, "compose_digest="+digest, "compose_digest=bad")
				case "bad-activated":
					body = strings.ReplaceAll(body, "activated_at_epoch=1791919000", "activated_at_epoch=0")
				case "old-retirement":
					body = strings.ReplaceAll(body, "retirement_routing_epoch=308", "retirement_routing_epoch=307")
				case "unreserved-retirement":
					body = strings.ReplaceAll(body, "retirement_routing_epoch=308", "retirement_routing_epoch=unreserved")
				case "bad-source-state":
					body = strings.ReplaceAll(body, "source_receipt_state=active", "source_receipt_state=retired")
				case "bad-source-sha":
					body = strings.ReplaceAll(body, "source_receipt_sha256="+strings.Repeat("e", 64), "source_receipt_sha256=bad")
				case "foreign-cohort":
					body = strings.ReplaceAll(body, "cohort=cdom", "cohort=c1")
				case "foreign-image":
					body = strings.ReplaceAll(body, image, "ghcr.io/example/jobseek-crawler:latest")
				case "foreign-revision":
					body = strings.ReplaceAll(body, revision, strings.Repeat("f", 40))
				}
				if err := os.WriteFile(path, []byte(body), 0600); err != nil {
					t.Fatal(err)
				}
				if corruption == "unsafe-mode" {
					if err := os.Chmod(path, 0644); err != nil {
						t.Fatal(err)
					}
				}
				if corruption == "symlink" || corruption == "hardlink" {
					link := filepath.Join(root, "link")
					var err error
					if corruption == "symlink" {
						err = os.Symlink(path, link)
					} else {
						err = os.Link(path, link)
					}
					if err != nil {
						t.Fatal(err)
					}
					path = link
				}
				for _, attest := range []bool{false, true} {
					args := []string{path, "cdom", "production-b0", "lightpanda-b0", image, revision}
					function := "load_receipt_identity"
					if attest {
						args = []string{path, "cdom", "production-b0", "lightpanda-b0", "307", image, revision, digest, state}
						function = "attest_receipt"
					}
					want, accepted := originalB0Reply(t, function, args)
					got, err := b0ReceiptReply(args, attest)
					if (err == nil) != accepted || accepted && got != want {
						t.Fatalf("attest=%t receipt oracle differs: original_accept=%t native_error=%v", attest, accepted, err)
					}
				}
			})
		}
	}
}

func TestB0EpochRepliesMatchDeliveredPythonHelpers(t *testing.T) {
	for _, body := range []string{`{"routing_epoch":307}`, `{"routing_epoch":307,"current":true}`, `{"routing_epoch":307,"current":false}`, `{"routing_epoch":true}`, `{"routing_epoch":307.0}`, `{"routing_epoch":3.07e2}`, `{"routing_epoch":0}`, `{"routing_epoch":10000000000000}`, `{"routing_epoch":307,"unknown":true}`, `null`, `[]`, `{}`, `{"routing_epoch":307} {}`} {
		for _, attest := range []bool{false, true} {
			function, expected := "reserve_routing_epoch", ""
			args := []string{body}
			if attest {
				function, expected = "attest_routing_epoch", "307"
				args = append(args, expected)
			}
			want, accepted := originalB0Reply(t, function, args)
			got, err := b0EpochReply(body, expected)
			if (err == nil) != accepted || accepted && got != want {
				t.Fatalf("epoch oracle differs: attest=%t input=%s native_error=%v", attest, body, err)
			}
		}
	}
}

func TestB0PathSyncPreservesNoFollowAndDirectoryFlags(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "receipt")
	if err := os.WriteFile(file, []byte("retained receipt\n"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"file", "directory"} {
		path := file
		if kind == "directory" {
			path = root
		}
		if err := b0PathSync(kind, path); err != nil {
			t.Fatal("valid receipt path was not synced", err)
		}
		link := filepath.Join(root, "link-"+kind)
		if err := os.Symlink(path, link); err != nil {
			t.Fatal(err)
		}
		if err := b0PathSync(kind, link); err == nil {
			t.Fatal("receipt fsync followed a symlink")
		}
	}
	if b0PathSync("directory", file) == nil || b0PathSync("unsupported", file) == nil {
		t.Fatal("invalid directory or path kind accepted")
	}
}
