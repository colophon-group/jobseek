package main

import (
	"bytes"
	"context"
	"debug/buildinfo"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

type failingBuildIdentityWriter struct{}

func (failingBuildIdentityWriter) Write([]byte) (int, error) {
	return 0, errors.New("fixture write failure")
}

func TestBuildIdentityRefusesMissingSourceAndWriteFailure(t *testing.T) {
	prior := sourceRevision
	t.Cleanup(func() { sourceRevision = prior })
	for _, invalid := range []string{"", strings.Repeat("a", 39), strings.Repeat("A", 40), strings.Repeat("g", 40)} {
		sourceRevision = invalid
		var output bytes.Buffer
		if writeBuildIdentity(&output) == nil || output.Len() != 0 {
			t.Fatal("invalid linked source emitted build identity")
		}
	}
	sourceRevision = strings.Repeat("a", 40)
	if writeBuildIdentity(failingBuildIdentityWriter{}) == nil {
		t.Fatal("build identity write failure ignored")
	}
}

func TestTrimpathBuildIdentityReportsLinkedSourceWithoutRuntimeAuthority(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	binary := filepath.Join(t.TempDir(), "supervisor")
	source := strings.Repeat("b", 40)
	build := exec.CommandContext(ctx, "go", "build", "-buildvcs=false", "-trimpath", "-ldflags=-s -w -X main.sourceRevision="+source, "-o", binary, ".")
	for _, variable := range os.Environ() {
		key, _, _ := strings.Cut(variable, "=")
		if key != "GOOS" && key != "GOARCH" && key != "GOFLAGS" && key != "CGO_ENABLED" {
			build.Env = append(build.Env, variable)
		}
	}
	build.Env = append(build.Env, "GOOS="+runtime.GOOS, "GOARCH="+runtime.GOARCH, "CGO_ENABLED=0", "GOFLAGS=")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("trimpath fixture build: %v: %s", err, output)
	}
	info, err := buildinfo.ReadFile(binary)
	if err != nil {
		t.Fatal(err)
	}
	for _, setting := range info.Settings {
		if setting.Key == "-ldflags" {
			t.Fatal("fixture no longer reproduces missing trimpath linker metadata")
		}
	}
	command := exec.CommandContext(ctx, binary, "--build-info")
	// A metadata query must succeed independently of runtime/producer config.
	command.Env = []string{"REDIS_URL=invalid", "LIGHTPANDA_B0_SUPERVISOR_MODE=invalid", "LIGHTPANDA_B0_PRODUCER_MODE=invalid", "LIGHTPANDA_B0_ROUTING_EPOCH=invalid", "CRAWLER_SOURCE_REVISION=" + strings.Repeat("c", 40)}
	output, err := command.Output()
	if err != nil {
		t.Fatalf("read-only linked identity: %v", err)
	}
	var identity map[string]string
	if json.Unmarshal(output, &identity) != nil || len(identity) != 5 || identity["schema"] != buildIdentitySchema || identity["source_revision"] != source || identity["goos"] != runtime.GOOS || identity["goarch"] != runtime.GOARCH || identity["go_version"] != info.GoVersion {
		t.Fatalf("linked identity mismatch: %s", output)
	}
	extra := exec.CommandContext(ctx, binary, "--build-info", "producer")
	extra.Env = command.Env
	if extra.Run() == nil {
		t.Fatal("extra build-info arguments admitted")
	}
}
