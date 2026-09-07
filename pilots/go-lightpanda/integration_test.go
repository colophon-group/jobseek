package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"strings"
	"testing"
)

const lightpandaStable040SHA256 = "bfcf9bd7e80939b87232aa114a49d8f397f51af0c2632d9fc58d4a6d4386624f"

func TestLightpandaIntegration(t *testing.T) {
	if runtime.GOOS != "linux" || runtime.GOARCH != "amd64" {
		t.Skip("stable Lightpanda 0.4.0 integration binary is Linux x86_64 only")
	}
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("set LIGHTPANDA_INTEGRATION_BIN to opt in")
	}
	if err := verifyFileSHA256(binary, lightpandaStable040SHA256); err != nil {
		t.Fatal(err)
	}

	origin := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/fixture" {
			http.NotFound(writer, request)
			return
		}
		writer.Header().Set("Content-Type", "text/html; charset=utf-8")
		writer.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(writer, `<!doctype html><html><head><title>Lightpanda fixture</title></head><body><main id="fixture">local only</main></body></html>`)
	}))
	defer origin.Close()

	t.Setenv("LIGHTPANDA_BIN", binary)
	var output bytes.Buffer
	exitCode := runCLI([]string{origin.URL + "/fixture", "document.title"}, &output)
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
	exitCode = runCLI([]string{origin.URL + "/fixture", "null"}, &output)
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
		exitCode = runCLI([]string{origin.URL + "/fixture", test.expression}, &output)
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
