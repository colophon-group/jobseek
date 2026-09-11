package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

var lightpandaStable040SHA256 = map[string]string{
	"amd64": "bfcf9bd7e80939b87232aa114a49d8f397f51af0c2632d9fc58d4a6d4386624f",
	"arm64": "5e3b54deed642ffeb2b8f24a1931e54c51161f44d9d728135da3d4863cb722fb",
}

func TestLightpandaIntegration(t *testing.T) {
	expectedSHA256, supported := lightpandaStable040SHA256[runtime.GOARCH]
	if runtime.GOOS != "linux" || !supported {
		t.Skip("stable Lightpanda 0.4.0 integration binary requires Linux amd64 or arm64")
	}
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("set LIGHTPANDA_INTEGRATION_BIN to opt in")
	}
	if err := verifyFileSHA256(binary, expectedSHA256); err != nil {
		t.Fatal(err)
	}

	origin := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/fixture" {
			http.NotFound(writer, request)
			return
		}
		writer.Header().Set("Content-Type", "text/html; charset=utf-8")
		writer.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(writer, `<!doctype html><html><head><title>Lightpanda fixture</title></head><body><main id="fixture">local only</main></body></html>`)
	}))

	t.Setenv("LIGHTPANDA_BIN", binary)
	fixtureRunner := testOnlyFixtureRunner(binary, "127.0.0.2")
	var output bytes.Buffer
	exitCode := runCLIWithRunner(
		[]string{origin.URL + "/fixture", "document.title"},
		&output,
		fixtureRunner,
	)
	if exitCode != 0 {
		t.Fatalf("runCLI exit code = %d, output = %s", exitCode, output.String())
	}

	var response cliResponse
	if err := json.Unmarshal(output.Bytes(), &response); err != nil {
		t.Fatalf("decode CLI output: %v (output = %s)", err, output.String())
	}
	if !response.OK || response.Result == nil {
		t.Fatalf("unexpected CLI response: %+v", response)
	}
	if response.Result.Status != http.StatusCreated {
		t.Errorf("status = %d, want %d", response.Result.Status, http.StatusCreated)
	}
	if response.Result.FinalURL != origin.URL+"/fixture" {
		t.Errorf("final URL = %q, want %q", response.Result.FinalURL, origin.URL+"/fixture")
	}
	if !bytes.Contains([]byte(response.Result.HTML), []byte(`id="fixture"`)) {
		t.Errorf("HTML omitted fixture marker: %q", response.Result.HTML)
	}
	if string(response.Result.Expression) != `"Lightpanda fixture"` {
		t.Errorf("expression = %s, want %q", response.Result.Expression, "Lightpanda fixture")
	}

	output.Reset()
	exitCode = runCLIWithRunner([]string{origin.URL + "/fixture", "null"}, &output, fixtureRunner)
	if exitCode != 0 {
		t.Fatalf("null expression exit code = %d, output = %s", exitCode, output.String())
	}
	response = cliResponse{}
	if err := json.Unmarshal(output.Bytes(), &response); err != nil {
		t.Fatalf("decode null CLI output: %v (output = %s)", err, output.String())
	}
	if !response.OK || response.Result == nil || string(response.Result.Expression) != "null" {
		t.Errorf("null expression was not preserved: %+v", response)
	}

	for _, test := range []struct {
		expression string
		wantError  string
	}{
		{expression: "undefined", wantError: `non-JSON type "undefined"`},
		{expression: "() => 1", wantError: `non-JSON type "function"`},
		{expression: `Symbol("x")`, wantError: "Object couldn't be returned by value (-32000)"},
	} {
		output.Reset()
		exitCode = runCLIWithRunner([]string{origin.URL + "/fixture", test.expression}, &output, fixtureRunner)
		if exitCode == 0 {
			t.Errorf("expression %q succeeded with output %s", test.expression, output.String())
		}
		response = cliResponse{}
		if err := json.Unmarshal(output.Bytes(), &response); err != nil {
			t.Fatalf("decode %q CLI output: %v (output = %s)", test.expression, err, output.String())
		}
		if response.OK || !strings.Contains(response.Error, test.wantError) {
			t.Errorf("expression %q did not fail with %q: %+v", test.expression, test.wantError, response)
		}
	}
}

func TestLightpandaRuntimeV1BridgeIntegration(t *testing.T) {
	expectedSHA256, supported := lightpandaStable040SHA256[runtime.GOARCH]
	if runtime.GOOS != "linux" || !supported {
		t.Skip("stable Lightpanda 0.4.0 integration binary requires Linux amd64 or arm64")
	}
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("set LIGHTPANDA_INTEGRATION_BIN to opt in")
	}
	if err := verifyFileSHA256(binary, expectedSHA256); err != nil {
		t.Fatal(err)
	}

	origin := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/runtime-v1-fixture" {
			http.NotFound(writer, request)
			return
		}
		writer.Header().Set("Content-Type", "text/html; charset=utf-8")
		writer.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(writer, `<!doctype html><html><head><title>Runtime v1 fixture</title></head><body><main id="runtime-v1-fixture">local only</main></body></html>`)
	}))

	for _, test := range []struct {
		name       string
		expression string
	}{
		{name: "B0"},
		{name: "B1", expression: "document.title"},
	} {
		t.Run(test.name, func(t *testing.T) {
			privacy := &bridgeFixturePrivacy{}
			adapter, err := lightpandaadapter.New(
				runtimeV1Runner{
					config: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()},
					run:    testOnlyFixtureRunner(binary, "127.0.0.2"),
				},
				privacy,
			)
			if err != nil {
				t.Fatal(err)
			}
			input := bridgeInput(origin.URL+"/runtime-v1-fixture", test.expression, 1024)
			input.Plan.Navigation.TimeoutMs = uint64(defaultTaskTimeout / time.Millisecond)
			result := adapter.Execute(context.Background(), input)
			if result.GetSuccess() == nil {
				t.Fatalf("runtime-v1 bridge failed: %v", result)
			}
			success := result.GetSuccess()
			if success.GetStatus() != http.StatusCreated || success.FinalUrl != input.Plan.TargetUrl ||
				!bytes.Contains(bridgeManifestBody(success.Html), []byte(`id="runtime-v1-fixture"`)) {
				t.Fatalf("unexpected bridge result: %v", success)
			}
			if test.expression == "" {
				if len(success.Evaluations) != 0 || len(privacy.calls) != 0 {
					t.Fatalf("B0 evaluated unexpectedly: %v/%#v", success.Evaluations, privacy.calls)
				}
			} else if len(success.Evaluations) != 1 || len(privacy.calls) != 1 ||
				string(success.Evaluations[0].Value.Payload) != `"Runtime v1 fixture"` {
				t.Fatalf("B1 evaluation was not preserved: %v/%#v", success.Evaluations, privacy.calls)
			}
		})
	}
}

func TestLightpandaRuntimeV1StdioIntegration(t *testing.T) {
	expectedSHA256, supported := lightpandaStable040SHA256[runtime.GOARCH]
	if runtime.GOOS != "linux" || !supported {
		t.Skip("stable Lightpanda 0.4.0 integration binary requires Linux amd64 or arm64")
	}
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("set LIGHTPANDA_INTEGRATION_BIN to opt in")
	}
	if err := verifyFileSHA256(binary, expectedSHA256); err != nil {
		t.Fatal(err)
	}

	origin := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/runtime-v1-stdio-fixture" {
			http.NotFound(writer, request)
			return
		}
		writer.Header().Set("Content-Type", "text/html; charset=utf-8")
		writer.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(writer, `<!doctype html><html><body><main id="runtime-v1-stdio-fixture">local only</main></body></html>`)
	}))

	adapter, err := lightpandaadapter.NewRenderOnly(
		runtimeV1Runner{
			config: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()},
			run:    testOnlyFixtureRunner(binary, "127.0.0.2"),
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	input := bridgeInput(origin.URL+"/runtime-v1-stdio-fixture", "", 0)
	input.Plan.Navigation.TimeoutMs = uint64(defaultTaskTimeout / time.Millisecond)
	var output bytes.Buffer
	if code := runRuntimeV1Stdio(context.Background(), runtimeV1BorrowedInput(frameRuntimeV1Input(t, input)), &output, adapter); code != 0 {
		t.Fatalf("runtime-v1 stdio exit code = %d", code)
	}
	result := decodeRuntimeV1Result(t, output.Bytes())
	if result.GetSuccess() == nil || result.GetSuccess().GetStatus() != http.StatusCreated ||
		result.GetSuccess().FinalUrl != input.Plan.TargetUrl ||
		!bytes.Contains(bridgeManifestBody(result.GetSuccess().Html), []byte(`id="runtime-v1-stdio-fixture"`)) {
		t.Fatalf("unexpected runtime-v1 stdio result: %v", result)
	}
}

func verifyFileSHA256(path string, expected string) error {
	file, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("open Lightpanda integration binary: %w", err)
	}
	defer file.Close()

	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return fmt.Errorf("hash Lightpanda integration binary: %w", err)
	}
	actual := fmt.Sprintf("%x", hash.Sum(nil))
	if actual != expected {
		return fmt.Errorf("Lightpanda integration binary SHA-256 = %s, want %s", actual, expected)
	}
	return nil
}
