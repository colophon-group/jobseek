package workable

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type scriptedResponse struct {
	status int
	body   string
	header http.Header
}

type scriptedClient struct {
	responses []scriptedResponse
	requests  []*http.Request
}

func (client *scriptedClient) Do(request *http.Request) (*http.Response, error) {
	client.requests = append(client.requests, request)
	if len(client.responses) == 0 {
		return nil, errors.New("unexpected extra Workable request")
	}
	response := client.responses[0]
	client.responses = client.responses[1:]
	return &http.Response{
		StatusCode: response.status,
		Body:       io.NopCloser(strings.NewReader(response.body)),
		Header:     response.header,
		Request:    request,
	}, nil
}

func noPause(context.Context, time.Duration) error { return nil }

func TestFetchPaginatesAndDeduplicates(t *testing.T) {
	client := &scriptedClient{responses: []scriptedResponse{
		{status: 200, body: `{"results":[{"shortcode":"AAA"},{"shortcode":"BBB"}],"nextPage":"cursor"}`},
		{status: 200, body: `{"results":[{"shortcode":"BBB"},{"shortcode":"CCC"}],"nextPage":null}`},
	}}
	result, err := Fetch(context.Background(), client, "acme", noPause)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"https://apply.workable.com/acme/j/AAA/",
		"https://apply.workable.com/acme/j/BBB/",
		"https://apply.workable.com/acme/j/CCC/",
	}
	if len(result.URLs) != len(want) || strings.Join(result.URLs, "\n") != strings.Join(want, "\n") {
		t.Fatalf("unexpected URL inventory: %#v", result.URLs)
	}
	if result.Requests != 2 || result.Responses != 2 || result.Truncated || result.VerifiedEmpty {
		t.Fatalf("unexpected summary: %#v", result)
	}
	first, err := io.ReadAll(client.requests[0].Body)
	if err != nil {
		t.Fatal(err)
	}
	second, err := io.ReadAll(client.requests[1].Body)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(first, []byte(`"token"`)) || !bytes.Contains(second, []byte(`"token":"cursor"`)) {
		t.Fatalf("opaque pagination token was not passed to the next POST: %s / %s", first, second)
	}
}

func TestFetchRateLimitUsesIndependentlyCountedMarkdown(t *testing.T) {
	responses := []scriptedResponse{}
	for range 4 {
		responses = append(responses, scriptedResponse{status: 429, body: `{"error":"rate limited"}`})
	}
	responses = append(responses,
		scriptedResponse{status: 200, body: "All open roles at Acme: 2 current openings"},
		scriptedResponse{status: 200, body: "https://apply.workable.com/acme/jobs/view/A.md\nhttps://apply.workable.com/acme/jobs/view/B.md"},
	)
	client := &scriptedClient{responses: responses}
	result, err := Fetch(context.Background(), client, "acme", noPause)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.URLs) != 2 || result.Requests != 6 || result.Truncated || result.VerifiedEmpty {
		t.Fatalf("unexpected fallback result: %#v", result)
	}
	if client.requests[4].URL.Path != "/acme/llms.txt" || client.requests[5].URL.Path != "/acme/jobs.md" {
		t.Fatalf("fallback requested wrong endpoints: %s %s", client.requests[4].URL, client.requests[5].URL)
	}
}

func TestFetchRateLimitVerifiesEmpty(t *testing.T) {
	responses := []scriptedResponse{}
	for range 4 {
		responses = append(responses, scriptedResponse{status: 429})
	}
	responses = append(responses, scriptedResponse{status: 200, body: "All open roles at Acme: 0 current openings"})
	result, err := Fetch(context.Background(), &scriptedClient{responses: responses}, "acme", noPause)
	if err != nil || !result.VerifiedEmpty || len(result.URLs) != 0 || result.Requests != 5 {
		t.Fatalf("verified empty fallback failed: %#v, %v", result, err)
	}
}

func TestFetchFailureAfterFirstPageCannotReturnPartialInventory(t *testing.T) {
	client := &scriptedClient{responses: []scriptedResponse{
		{status: 200, body: `{"results":[{"shortcode":"A"}],"nextPage":"cursor"}`},
		{status: 429}, {status: 429}, {status: 429}, {status: 429},
	}}
	result, err := Fetch(context.Background(), client, "acme", noPause)
	if err == nil || len(result.URLs) != 0 {
		t.Fatalf("partial inventory became success: %#v, %v", result, err)
	}
}

func TestFetchRetriesMalformedJSONAndRejectsTDM(t *testing.T) {
	client := &scriptedClient{responses: []scriptedResponse{
		{status: 200, body: `{"results":`},
		{status: 200, body: `{"results":[{"shortcode":"A"}]}`},
	}}
	result, err := Fetch(context.Background(), client, "acme", noPause)
	if err != nil || len(result.URLs) != 1 || result.Requests != 2 {
		t.Fatalf("malformed JSON retry failed: %#v, %v", result, err)
	}
	reserved := &scriptedClient{responses: []scriptedResponse{{status: 200, header: http.Header{"Tdm-Reservation": {"1"}}}}}
	result, err = Fetch(context.Background(), reserved, "acme", noPause)
	if err == nil || result.ErrorKind != "tdm" || result.Requests != 1 {
		t.Fatalf("TDM reservation was ignored: %#v, %v", result, err)
	}
}

func TestMarkdownCountMismatchFailsClosed(t *testing.T) {
	responses := []scriptedResponse{}
	for range 4 {
		responses = append(responses, scriptedResponse{status: 429})
	}
	responses = append(responses,
		scriptedResponse{status: 200, body: "All open roles at Acme: 2 current openings"},
		scriptedResponse{status: 200, body: "https://apply.workable.com/acme/jobs/view/A.md"},
	)
	result, err := Fetch(context.Background(), &scriptedClient{responses: responses}, "acme", noPause)
	if err == nil || len(result.URLs) != 0 {
		t.Fatalf("count mismatch became success: %#v, %v", result, err)
	}
}
