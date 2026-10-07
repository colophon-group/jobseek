package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/netip"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type replayRoundTrip func(*http.Request) (*http.Response, error)

func (f replayRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestAPIReplayHTTPFallbackUsesRefreshedCredentialsAndHTTPPageBudget(t *testing.T) {
	var task Task
	var inventory api.Inventory
	task, err := newAPIReplayTask("https://example.com/careers", replayControllerMetadata, func(ctx context.Context, fetch api.Fetch, usingHTTP bool) error {
		if !usingHTTP {
			t.Fatal("fallback did not select HTTP page budget")
		}
		var err error
		inventory, err = api.DiscoverBrowserReplay(ctx, task.APIReplay.options, fetch, replayControllerJoin, usingHTTP)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	browserCalls, httpCalls := 0, 0
	client := &http.Client{Transport: replayRoundTrip(func(r *http.Request) (*http.Response, error) {
		httpCalls++
		if r.Header.Get("X-Csrf-Token") != "new-private-csrf" || r.Header.Get("Host") != "" || r.Method != "POST" {
			t.Fatal("fallback changed private headers/method")
		}
		body := `{"jobs":[{"id":"` + r.URL.Query().Get("page") + `","title":"Engineer"}]}`
		if httpCalls == 1 {
			body = `{"jobs":[{"id":"1","title":"Engineer"}]}`
		}
		if httpCalls == 60 {
			body = `{"jobs":[]}`
		}
		if httpCalls == 61 {
			body = `{"jobs":[]}`
		}
		return &http.Response{StatusCode: 200, Header: http.Header{}, Request: r, Body: io.NopCloser(strings.NewReader(body))}, nil
	})}
	err = converseAPIReplay(context.Background(), task.APIReplay, http.Header{"X-Csrf-Token": {"new-private-csrf"}, "Host": {"example.com"}}, nil, func(context.Context, api.Request) (*api.Document, error) {
		browserCalls++
		return nil, errReplayCapture
	}, replayHTTPFetcher(task.APIReplay.options, client))
	if err != nil || browserCalls != 1 || httpCalls != 61 || len(inventory.Jobs) != 59 {
		t.Fatal("fallback traversal switched late, replayed first page or used browser50 budget", err, browserCalls, httpCalls, len(inventory.Jobs))
	}
}

func TestAPIReplayDoesNotFallbackOnTerminalErrorsOrLaterPageFailure(t *testing.T) {
	for _, terminal := range []error{&policy.Reservation{URL: "https://example.com/api"}, policy.ErrSignals, errReplayCredentialResponse, errResourceLimit, context.Canceled} {
		task, _ := newAPIReplayTask("https://example.com/careers", replayControllerMetadata, func(context.Context, api.Fetch, bool) error {
			t.Fatal("terminal initial failure entered conversation")
			return nil
		})
		err := converseAPIReplay(context.Background(), task.APIReplay, http.Header{}, nil, func(context.Context, api.Request) (*api.Document, error) { return nil, terminal }, func(context.Context, api.Request) (*api.Document, error) {
			t.Fatal("terminal initial failure used fallback")
			return nil, nil
		})
		if !errors.Is(err, terminal) {
			t.Fatal("terminal error lost", err)
		}
	}
	var task Task
	task, _ = newAPIReplayTask("https://example.com/careers", replayControllerMetadata, func(ctx context.Context, fetch api.Fetch, httpFallback bool) error {
		_, err := api.DiscoverBrowserReplay(ctx, task.APIReplay.options, fetch, replayControllerJoin, httpFallback)
		return err
	})
	first, _ := api.Decode([]byte(`{"total":2,"jobs":[{"id":"1","title":"Engineer"}]}`))
	err := converseAPIReplay(context.Background(), task.APIReplay, http.Header{}, first, func(context.Context, api.Request) (*api.Document, error) { return nil, errReplayCapture }, func(context.Context, api.Request) (*api.Document, error) {
		t.Fatal("later page failure changed transport")
		return nil, nil
	})
	if !errors.Is(err, errReplayCapture) {
		t.Fatal("later failure was hidden", err)
	}
}

func TestAPIReplayHTTPChecksPolicyOnFailureAndRejectsReflection(t *testing.T) {
	o := replayFetchOptions(t)
	for _, c := range []struct {
		status int
		body   string
		header http.Header
		want   error
	}{
		{403, `{}`, http.Header{"Tdm-Reservation": {"1"}}, nil},
		{200, `{"jobs":[{"title":"private-test-token"}]}`, http.Header{}, errReplayCredentialResponse},
		{200, strings.Repeat("x", replayCaptureBodyLimit+1), http.Header{}, errResourceLimit},
	} {
		fetch := replayHTTPFetcher(o, &http.Client{Transport: replayRoundTrip(func(r *http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: c.status, Header: c.header, Body: io.NopCloser(strings.NewReader(c.body)), Request: r}, nil
		})})
		_, err := fetch(context.Background(), api.Request{Method: o.Inventory.Method, URL: o.Inventory.Endpoint, Headers: http.Header{"Authorization": {"Bearer private-test-token"}}})
		if c.want != nil {
			if !errors.Is(err, c.want) {
				t.Fatal("HTTP response violated terminal policy/privacy/bounds", err)
			}
		} else {
			var reservation *policy.Reservation
			if !errors.As(err, &reservation) {
				t.Fatal("HTTP status suppressed publisher denial", err)
			}
		}
	}
}

func TestAPIReplayHTTPDialUsesWholeTrustedDenyInventory(t *testing.T) {
	policy, err := newEgressPolicy([]string{"8.8.8.0/24"})
	if err != nil {
		t.Fatal(err)
	}
	prefixes := []netip.Prefix{}
	for _, entry := range strings.Split(policy.blockCIDRs, ",") {
		prefixes = append(prefixes, netip.MustParsePrefix(entry))
	}
	for _, raw := range []string{"127.0.0.1", "10.0.0.5", "169.254.169.254", "8.8.8.8", "::ffff:127.0.0.1", "2001:db8::1"} {
		if replayHTTPAddressesAllowed(prefixes, []netip.Addr{netip.MustParseAddr("1.1.1.1"), netip.MustParseAddr(raw)}) {
			t.Fatal("mixed DNS answer bypassed trusted deny inventory", raw)
		}
	}
	if !replayHTTPAddressesAllowed(prefixes, []netip.Addr{netip.MustParseAddr("1.1.1.1")}) || replayHTTPAddressesAllowed(prefixes, nil) {
		t.Fatal("public/empty answer contract differs")
	}
	if _, _, err := newReplayHTTPFallback(replayFetchOptions(t), EgressPolicy{}); err == nil {
		t.Fatal("fallback accepted unqualified startup policy")
	}
}
