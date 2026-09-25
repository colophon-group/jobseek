package lever

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request)
}

func TestFetchPaginatesAtExactlyOneHundred(t *testing.T) {
	firstPage := strings.Repeat(`{"hostedUrl":"https://jobs.lever.co/acme/1"},`, BatchSize)
	firstPage = "[" + strings.TrimSuffix(firstPage, ",") + "]"
	requests := 0
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		requests++
		if request.Header.Get("Accept") != "application/json" || request.URL.Host != "api.lever.co" {
			t.Fatalf("unexpected request %s", request.URL)
		}
		body := firstPage
		if request.URL.Query().Get("skip") == "100" {
			body = `[{"hostedUrl":"https://jobs.lever.co/acme/2"}]`
		} else if request.URL.Query().Get("skip") != "0" {
			t.Fatalf("unexpected page %s", request.URL)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: http.Header{}, Request: request}, nil
	})}
	result, err := Fetch(context.Background(), client, "acme", "")
	if err != nil {
		t.Fatal(err)
	}
	if requests != 2 || result.Requests != 2 || result.Responses != 2 || result.LastSkip != 100 || len(result.Jobs) != 101 || result.Truncated {
		t.Fatalf("unexpected complete inventory: %+v", result)
	}
}

func TestFetchFailsClosedOnLaterPage(t *testing.T) {
	firstPage := strings.Repeat(`{"hostedUrl":"https://jobs.lever.co/acme/1"},`, BatchSize)
	firstPage = "[" + strings.TrimSuffix(firstPage, ",") + "]"
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Query().Get("skip") == "100" {
			return &http.Response{StatusCode: 403, Body: io.NopCloser(strings.NewReader("blocked")), Header: http.Header{}, Request: request}, nil
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(firstPage)), Header: http.Header{}, Request: request}, nil
	})}
	result, err := Fetch(context.Background(), client, "acme", "")
	if err == nil || len(result.Jobs) != 0 || result.Status != 403 || result.Requests != 2 {
		t.Fatalf("partial inventory was accepted: result=%+v error=%v", result, err)
	}
}

func TestTokenURLRejectsNonCanonicalInputs(t *testing.T) {
	for _, input := range []struct{ token, region string }{{"../host", ""}, {"acme", "us"}, {"acme?x=1", "eu"}} {
		if _, err := TokenURL(input.token, input.region, 0); err == nil {
			t.Fatal(fmt.Sprintf("accepted %q %q", input.token, input.region))
		}
	}
	url, err := TokenURL("acme", "eu", 100)
	if err != nil || url != "https://api.eu.lever.co/v0/postings/acme?limit=100&skip=100" {
		t.Fatalf("unexpected EU URL %s: %v", url, err)
	}
}
