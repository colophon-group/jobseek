package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestHygieneReadsOnlyInventoryAndPreservesOriginalResourceSelection(t *testing.T) {
	now, _ := time.Parse(time.RFC3339, "2026-07-20T12:00:00Z")
	calls := []string{}
	command := func(_ context.Context, name string, args ...string) ([]byte, error) {
		key := name + " " + strings.Join(args, " ")
		calls = append(calls, key)
		switch key {
		case "docker ps -q":
			return []byte("old managed young future"), nil
		case "docker inspect old managed young future":
			return []byte(`[
 {"Id":"old","Name":"/debug","Config":{"Image":"fixture","Env":["SECRET=never-report"],"Labels":{}},"State":{"StartedAt":"2026-07-18T10:00:00.123456789Z"}},
 {"Id":"managed","Name":"/worker","Config":{"Labels":{"com.docker.compose.project":"deploy"}},"State":{"StartedAt":"invalid-but-managed"}},
 {"Id":"young","Name":"/young","State":{"StartedAt":"2026-07-20T11:30:00Z"}},
 {"Id":"future","Name":"/future","State":{"StartedAt":"2026-07-21T12:00:00Z"}}
]`), nil
		case "systemctl list-units --type=service --state=active --no-legend --plain":
			return []byte("old.service loaded active exited fixture\nmanaged.service loaded active exited fixture\nrecent.service loaded active exited fixture\nrunning.service loaded active running fixture"), nil
		case "systemctl show old.service --property=FragmentPath --property=ActiveEnterTimestampMonotonic --no-pager":
			return []byte("FragmentPath=/run/systemd/transient/old.service\nActiveEnterTimestampMonotonic=500000000000\n"), nil
		case "systemctl show managed.service --property=FragmentPath --property=ActiveEnterTimestampMonotonic --no-pager":
			return []byte("FragmentPath=/etc/systemd/system/managed.service\nActiveEnterTimestampMonotonic=1\n"), nil
		case "systemctl show recent.service --property=FragmentPath --property=ActiveEnterTimestampMonotonic --no-pager":
			return []byte("FragmentPath=/run/systemd/transient/recent.service\nActiveEnterTimestampMonotonic=999000000000\n"), nil
		default:
			t.Fatalf("unexpected host command: %s", key)
			return nil, nil
		}
	}
	findings, err := inspectHostHygiene(context.Background(), command, now, 1e6, 86400)
	if err != nil || len(findings) != 2 {
		t.Fatal(findings, err)
	}
	if findings[0].name != "debug" || hygieneAge(findings[0].ageSeconds) != "2d01h" || findings[1].name != "old.service" {
		t.Fatal("resource identity or age differs", findings)
	}
	for _, finding := range findings {
		if strings.Contains(fmt.Sprint(finding), "never-report") || strings.Contains(finding.cleanup, "docker rm") {
			t.Fatal("inventory leaked environment or recommended unsafe cleanup")
		}
	}
	if len(calls) != 6 {
		t.Fatal("unexpected inventory command count", calls)
	}
}

func TestHygieneRefusesIncompleteHostEvidence(t *testing.T) {
	for _, mode := range []string{"command", "invalid-json", "empty-inspect", "invalid-started", "missing-timezone", "invalid-monotonic", "systemctl-failed"} {
		t.Run(mode, func(t *testing.T) {
			command := func(_ context.Context, name string, args ...string) ([]byte, error) {
				if mode == "command" || mode == "systemctl-failed" && name == "systemctl" {
					return nil, errors.New("inspection unavailable")
				}
				if name == "docker" {
					if args[0] == "ps" {
						return []byte("one"), nil
					}
					switch mode {
					case "invalid-json":
						return []byte(`{"Env":["do-not-log"]`), nil
					case "empty-inspect":
						return []byte("[]"), nil
					case "invalid-started":
						return []byte(`[{"State":{"StartedAt":"do-not-log"}}]`), nil
					case "missing-timezone":
						return []byte(`[{"State":{"StartedAt":"2026-07-20T12:00:00"}}]`), nil
					default:
						return []byte(`[{"Config":{"Labels":{"com.docker.compose.project":"deploy"}}}]`), nil
					}
				}
				if args[0] == "list-units" {
					return []byte("bad.service loaded active exited fixture"), nil
				}
				return []byte("FragmentPath=/run/systemd/transient/bad.service\nActiveEnterTimestampMonotonic=invalid"), nil
			}
			findings, err := inspectHostHygiene(context.Background(), command, time.Now(), 1e6, 86400)
			if err == nil || findings != nil || strings.Contains(err.Error(), "do-not-log") {
				t.Fatal("unproved inventory accepted or raw response leaked", findings, err)
			}
		})
	}
}

func TestHygieneEmptyInventoryDoesNotInspectOrMutate(t *testing.T) {
	calls := []string{}
	command := func(_ context.Context, name string, args ...string) ([]byte, error) {
		calls = append(calls, name+" "+strings.Join(args, " "))
		return []byte(""), nil
	}
	findings, err := inspectHostHygiene(context.Background(), command, time.Now(), 100, 86400)
	if err != nil || len(findings) != 0 || !reflect.DeepEqual(calls, []string{"docker ps -q", "systemctl list-units --type=service --state=active --no-legend --plain"}) {
		t.Fatal(findings, err, calls)
	}
}

func TestHygieneRejectsInvalidOptionsAndUptimeBeforeHostInspection(t *testing.T) {
	file := filepath.Join(t.TempDir(), "uptime")
	if err := os.WriteFile(file, []byte("100.0 0.0"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"--max-age-hours=0"}, {"--max-age-hours=-1"}, {"--max-age-hours=NaN"}, {"--max-age-hours=Inf"}, {"--max-age-hours=1e308"}, {"--now=missing-timezone"}, {"--unknown"}, {"unexpected"}} {
		var stdout, stderr bytes.Buffer
		if runHostHygiene(append([]string{"--proc-uptime", file}, args...), &stdout, &stderr) != 2 || stdout.Len() != 0 {
			t.Fatal("invalid audit input accepted", args)
		}
	}
	for _, value := range []string{"", "NaN 0", "Inf 0", "-1 0", "bad"} {
		if err := os.WriteFile(file, []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
		var stdout, stderr bytes.Buffer
		if runHostHygiene([]string{"--proc-uptime", file}, &stdout, &stderr) != 2 {
			t.Fatal("invalid uptime accepted", value)
		}
	}
}

func TestHygieneCommandFailureDoesNotPrintRawHostOutput(t *testing.T) {
	command := filepath.Join(t.TempDir(), "failing-inspector")
	if err := os.WriteFile(command, []byte("#!/bin/sh\nprintf 'fixture-private-env'\nprintf 'fixture-private-env' >&2\nexit 1\n"), 0700); err != nil {
		t.Fatal(err)
	}
	body, err := runHygieneCommand(context.Background(), command)
	if err == nil || body != nil || strings.Contains(err.Error(), "fixture-private-env") {
		t.Fatal("raw host output entered diagnostic", err)
	}
}

func TestHygieneInventoryCopyCannotBypassOutputLimit(t *testing.T) {
	var output hygieneOutput
	reader := struct{ io.Reader }{strings.NewReader(strings.Repeat("x", (32<<20)+1))}
	if _, err := io.Copy(&output, reader); err == nil || output.Len() > 32<<20 {
		t.Fatal("streamed inventory bypassed the output bound", output.Len(), err)
	}
}
