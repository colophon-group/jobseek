package executor

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"testing"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

func renderedFixture(html, finalURL string, status uint32) *runtimev1.BrowserResult {
	body := []byte(html)
	digest := sha256.Sum256(body)
	manifest := &runtimev1.ChunkManifest{Complete: true, TotalSizeBytes: uint64(len(body)), TotalSha256: hex.EncodeToString(digest[:])}
	for offset := 0; offset < len(body); offset += HTMLChunkLimit {
		part := body[offset:min(offset+HTMLChunkLimit, len(body))]
		hash := sha256.Sum256(part)
		manifest.Chunks = append(manifest.Chunks, &runtimev1.DataChunk{Sequence: uint32(len(manifest.Chunks)), SizeBytes: uint64(len(part)), Sha256: hex.EncodeToString(hash[:]), Storage: &runtimev1.DataChunk_InlineBody{InlineBody: part}})
	}
	return &runtimev1.BrowserResult{ContractVersion: "crawler.runtime/v1", Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA, Outcome: &runtimev1.BrowserResult_Success{Success: &runtimev1.BrowserSuccess{FinalUrl: finalURL, Status: &status, Html: manifest}}}
}

func TestDirectRenderedParsersMatchFrozenPythonCorpus(t *testing.T) {
	for _, parser := range []string{"dom", "json-ld"} {
		path := "../dom-detail/testdata/python_cases.json"
		if parser == "json-ld" {
			path = "../jsonld-detail/testdata/python_cases.json"
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var cases []struct {
			Request struct {
				Mode, HTML, URL string
				Config          map[string]any
			}
			Expected      json.RawMessage
			Error         string
			ExpectedError string `json:"expected_error"`
		}
		if err := json.Unmarshal(raw, &cases); err != nil {
			t.Fatal(err)
		}
		checked := 0
		for _, c := range cases {
			if parser == "dom" && c.Request.Mode != "parse" {
				continue
			}
			config, err := json.Marshal(c.Request.Config)
			if err != nil {
				t.Fatal(err)
			}
			task := b0task.Task{Envelope: b0task.Envelope{ScraperType: parser, ParserConfig: config, SourceURL: c.Request.URL}}
			content, err := ParseRendered(task, renderedFixture(c.Request.HTML, "https://example.test/jobs/1", 200))
			if c.Error != "" || c.ExpectedError != "" {
				if err == nil {
					t.Fatalf("%s accepted frozen parser error", parser)
				}
				continue
			}
			if err != nil {
				t.Fatalf("%s case %d: %v", parser, checked, err)
			}
			actual, err := b0task.CanonicalJSON(content, false)
			if err != nil {
				t.Fatal(err)
			}
			expectedValue, err := b0task.ParseCanonicalValue(c.Expected)
			if err != nil {
				t.Fatal(err)
			}
			expected, err := b0task.CanonicalJSON(expectedValue, false)
			if err != nil {
				t.Fatal(err)
			}
			if string(actual) != string(expected) {
				t.Fatalf("%s case %d differs:\n%s\n%s", parser, checked, actual, expected)
			}
			checked++
		}
		if checked < 20 {
			t.Fatalf("%s corpus did not run: %d", parser, checked)
		}
	}
}

func TestDirectRenderedPolicyAndFailureBudgets(t *testing.T) {
	task := b0task.Task{Envelope: b0task.Envelope{ScraperType: "dom", SourceURL: "https://example.test/jobs/1", ParserConfig: json.RawMessage(`{"gone_url_pattern":"/gone$","steps":[]}`)}}
	_, err := ParseRendered(task, renderedFixture("<title>Just a moment</title>", "https://example.test/gone", 200))
	if FailureClass(err) != FailureGone {
		t.Fatal("gone redirect must precede bot challenge")
	}
	_, err = ParseRendered(task, renderedFixture("<title>Just a moment</title>", "https://example.test/jobs/1", 200))
	if !errors.Is(err, ErrBotChallenge) || FailureClass(err) != FailureTransient {
		t.Fatal("challenge consumed budget")
	}
	for _, c := range []struct {
		status   uint32
		url      string
		expected FailureDisposition
	}{
		{404, "https://example.test/jobs/1", FailureGone}, {410, "https://example.test/jobs/1", FailureGone},
		{401, "https://example.test/jobs/1", FailureTransient}, {403, "https://example.test/jobs/1", FailureTransient}, {429, "https://example.test/jobs/1", FailureTransient}, {500, "https://example.test/jobs/1", FailureTransient},
		{400, "https://example.test/jobs/1", FailureBudget}, {406, "https://example.test/jobs/1", FailureBudget},
		{406, "https://tenant.avature.net/en_US/JobDetail/Role/1", FailureTransient}, {406, "https://example.test/Careers/JobDetail/Role/1", FailureTransient}, {406, "https://example.test/FolderDetail/1", FailureTransient},
	} {
		_, err := ParseRendered(task, renderedFixture("", c.url, c.status))
		if FailureClass(err) != c.expected {
			t.Fatalf("status %d policy drift", c.status)
		}
	}
	invalid := renderedFixture("", "https://example.test/jobs/1", 404)
	invalid.GetSuccess().Html.Complete = false
	_, err = ParseRendered(task, invalid)
	if !errors.Is(err, ErrRenderedResult) || FailureClass(err) != FailureTransient {
		t.Fatal("invalid gone manifest consumed budget")
	}
}
