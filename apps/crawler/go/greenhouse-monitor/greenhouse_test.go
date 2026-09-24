package greenhouse

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestParseRichInventoryAndFailClosed(t *testing.T) {
	body := []byte(`{"jobs":[{"absolute_url":"https://job-boards.greenhouse.io/elastic/jobs/1","title":" Senior\t Engineer ","content":"<p>Role</p><a>Read more</a><a href='/x'>Learn more</a>","location":{"name":" New\tYork "},"offices":[{"name":"New York"},{"name":" Zürich "}],"departments":[{"name":"Engineering"}],"education":{"degree":"BS"},"requisition_id":"REQ-1","first_published":"2026-09-01T00:00:00Z","language":"en"}]}`)
	result, err := Parse(body)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Jobs) != 1 || result.Truncated {
		t.Fatalf("unexpected inventory: %+v", result)
	}
	job := result.Jobs[0]
	if job.Title == nil || *job.Title != "Senior Engineer" || job.Description == nil || *job.Description != "<p>Role</p><a href='/x'>Learn more</a>" {
		t.Fatalf("rich fields differ: %+v", job)
	}
	if len(job.Locations) != 2 || job.Locations[0] != "New York" || job.Locations[1] != "Zürich" {
		t.Fatalf("locations differ: %+v", job.Locations)
	}
	if _, err := Parse([]byte(`{"jobs":[{"title":"missing URL"},{"absolute_url":"https://example.com/job"}]}`)); err == nil {
		t.Fatal("partial inventory was accepted")
	}
	if _, err := Parse([]byte(`{"jobs":[{"absolute_url":"https://example.com/job","offices":null}]}`)); err == nil {
		t.Fatal("malformed office list was accepted")
	}
}

func TestFetchOneRequestAndGone(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		requests++
		if request.Method != http.MethodGet || request.URL.RawQuery != "content=true" || request.Header.Get("User-Agent") != userAgent {
			t.Errorf("publisher request changed: %+v", request)
		}
		writer.Header().Set("Content-Type", "application/json")
		io.WriteString(writer, `{"jobs":[]}`)
	}))
	defer server.Close()
	result, err := Fetch(context.Background(), server.Client(), server.URL+"/v1/boards/elastic/jobs?content=true")
	if err != nil || requests != 1 || result.Responses != 1 || result.Bytes != len(`{"jobs":[]}`) {
		t.Fatalf("one-request fetch failed: %+v, %v, requests=%d", result, err, requests)
	}
	goneServer := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusNotFound)
		io.WriteString(writer, "gone")
	}))
	defer goneServer.Close()
	result, err = Fetch(context.Background(), goneServer.Client(), goneServer.URL+"/v1/boards/elastic/jobs?content=true")
	if err == nil || !strings.Contains(err.Error(), "404") || result.Status != 404 {
		t.Fatalf("404 was not preserved: %+v, %v", result, err)
	}
}
