package workable

import (
	"context"
	"net/http"
	"strings"
	"testing"
)

const detailSource = "https://apply.workable.com/acme/j/ABC123/"

func TestDetailEndpointsAreCanonicalAndBounded(t *testing.T) {
	api, markdown, err := detailEndpoints(detailSource, "")
	if err != nil || api != "https://apply.workable.com/api/v2/accounts/acme/jobs/ABC123" || markdown != "https://apply.workable.com/acme/jobs/view/ABC123.md" {
		t.Fatalf("unexpected detail endpoints: %q %q %v", api, markdown, err)
	}
	for _, source := range []string{
		"http://apply.workable.com/acme/j/ABC123/",
		"https://evil.example/acme/j/ABC123/",
		"https://apply.workable.com/acme/j/ABC123/extra",
		"https://apply.workable.com/acme/j/ABC123/?q=1",
		"https://apply.workable.com/acme/j/%2e%2e/",
	} {
		if _, _, err := detailEndpoints(source, ""); err == nil {
			t.Errorf("accepted invalid Workable detail URL %q", source)
		}
	}
}

func TestFetchDetailProjectsJSONAndPreservesAccounting(t *testing.T) {
	client := &scriptedClient{responses: []scriptedResponse{{
		status: 200,
		body:   `{"title":"Engineer","description":"<p>Build</p>","requirements":"<p>Go</p>","locations":[{"city":"Zurich","country":"Switzerland"}],"workplace":"remote"}`,
	}}}
	result, err := fetchDetail(context.Background(), detailSource, "", client)
	if err != nil || result.Content == nil || result.Content.Title == nil || *result.Content.Title != "Engineer" {
		t.Fatalf("unexpected detail result: %#v %v", result, err)
	}
	if result.Requests != 1 || result.Responses != 1 || result.Status != 200 || len(client.requests) != 1 {
		t.Fatalf("unexpected detail accounting: %#v", result)
	}
	if result.Content.Description == nil || *result.Content.Description != "<p>Build</p>\n<p>Go</p>" {
		t.Fatalf("unexpected description: %#v", result.Content.Description)
	}
}

func TestFetchDetail429UsesOneMarkdownFallback(t *testing.T) {
	client := &scriptedClient{responses: []scriptedResponse{
		{status: 429, body: `{"error":"rate_limit"}`},
		{status: 200, body: "# Engineer\n\n## Description\n\nBuild things.\n\n## Apply\n"},
	}}
	result, err := fetchDetail(context.Background(), detailSource, "", client)
	if err != nil || result.Content == nil || result.Content.Description == nil || *result.Content.Description != "<p>Build things.</p>" {
		t.Fatalf("unexpected fallback result: %#v %v", result, err)
	}
	if result.Requests != 2 || result.Responses != 2 || result.Status != 200 || len(client.requests) != 2 ||
		client.requests[1].URL.String() != "https://apply.workable.com/acme/jobs/view/ABC123.md" {
		t.Fatalf("unexpected fallback accounting: %#v", result)
	}
}

func TestFetchDetailNon200StaysEmptyAndMalformed200Fails(t *testing.T) {
	notFound := &scriptedClient{responses: []scriptedResponse{{status: 404, body: "gone"}}}
	result, err := fetchDetail(context.Background(), detailSource, "", notFound)
	if err != nil || result.Content != nil || result.Status != 404 {
		t.Fatalf("unexpected empty detail: %#v %v", result, err)
	}
	fallbackFailed := &scriptedClient{responses: []scriptedResponse{
		{status: 429, body: `{"error":"rate_limit"}`},
		{status: 503, body: "unavailable"},
	}}
	result, err = fetchDetail(context.Background(), detailSource, "", fallbackFailed)
	if err != nil || result.Content != nil || result.Status != 503 || result.Requests != 2 {
		t.Fatalf("failed Markdown fallback should be empty: %#v %v", result, err)
	}
	malformed := &scriptedClient{responses: []scriptedResponse{{status: 200, body: "<html>blocked</html>"}}}
	result, err = fetchDetail(context.Background(), detailSource, "", malformed)
	if err == nil || result.ErrorKind != "invalid_payload" || result.Content != nil {
		t.Fatalf("malformed detail became success: %#v %v", result, err)
	}
}

func TestFetchDetailHonorsTDMReservation(t *testing.T) {
	client := &scriptedClient{responses: []scriptedResponse{{
		status: 200,
		body:   `{"title":"Engineer"}`,
		header: http.Header{"Tdm-Reservation": {"1"}},
	}}}
	result, err := fetchDetail(context.Background(), detailSource, "", client)
	if err == nil || !strings.Contains(err.Error(), "tdm-reservation=1") || result.ErrorKind != "tdm" {
		t.Fatalf("reservation was not enforced: %#v %v", result, err)
	}
}
