package main

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"testing"

	"github.com/chromedp/cdproto/network"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

const replayControllerMetadata = `{"browser":true,"api_url":"https://example.com/api","method":"POST","json_path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"pagination":{"param_name":"page","start_value":1,"increment":1,"style":"page"},"settle":0}`

func replayControllerJoin(base, path string) (string, error) {
	u, err := url.Parse(base)
	if err != nil {
		return "", err
	}
	r, err := url.Parse(path)
	if err != nil {
		return "", err
	}
	return u.ResolveReference(r).String(), nil
}

func TestAPIReplayConversationUsesCaptureThenSamePrivateHeadersForPagination(t *testing.T) {
	var retained api.Fetch
	var options api.BrowserReplayOptions
	var inventory api.Inventory
	task, err := newAPIReplayTask("https://example.com/careers", replayControllerMetadata, func(ctx context.Context, fetch api.Fetch) error {
		retained = fetch
		var err error
		inventory, err = api.DiscoverBrowserReplay(ctx, options, fetch, replayControllerJoin, false)
		return err
	})
	if err != nil || validateTask(task) != nil {
		t.Fatal("valid replay task rejected", err)
	}
	options = task.APIReplay.options
	first, _ := api.Decode([]byte(`{"total":3,"jobs":[{"id":"1","title":"One"}]}`))
	headers := http.Header{"Authorization": {"fresh-secret"}, "Host": {"example.com"}, "X-Csrf-Token": {"fresh-csrf"}}
	requests := 0
	err = converseAPIReplay(context.Background(), task.APIReplay, headers, first, func(_ context.Context, request api.Request) (*api.Document, error) {
		requests++
		if request.Headers.Get("Authorization") != "fresh-secret" || request.Headers.Get("X-Csrf-Token") != "fresh-csrf" || request.Headers.Get("Host") != "" {
			t.Fatal("private page headers differ")
		}
		u, _ := url.Parse(request.URL)
		page := u.Query().Get("page")
		if page != "2" && page != "3" {
			t.Fatal("captured first page was replayed", request.URL)
		}
		return api.Decode([]byte(`{"total":3,"jobs":[{"id":"` + page + `","title":"Later"}]}`))
	})
	if err != nil || requests != 2 || len(inventory.Jobs) != 3 || inventory.Truncated {
		t.Fatal("whole inventory differs", err, requests, len(inventory.Jobs))
	}
	if len(headers) != 0 {
		t.Fatal("private controller headers retained after conversation")
	}
	_, err = retained(context.Background(), api.Request{Method: "POST", URL: options.Inventory.Endpoint})
	if !errors.Is(err, errReplayCapture) || requests != 2 {
		t.Fatal("closed conversation issued a browser request", err)
	}
}

func TestAPIReplayConversationCannotSwallowPublisherDenialInProbe(t *testing.T) {
	for _, denial := range []error{&policy.Reservation{URL: "https://example.com/api", Source: "header"}, policy.ErrSignals} {
		task, err := newAPIReplayTask("https://example.com/careers", replayControllerMetadata, func(ctx context.Context, fetch api.Fetch) error {
			_, _ = fetch(ctx, api.Request{Method: "POST", URL: "https://example.com/api", Probe: true})
			return nil // Inventory size probes may suppress fetch errors.
		})
		if err != nil {
			t.Fatal(err)
		}
		err = converseAPIReplay(context.Background(), task.APIReplay, http.Header{}, nil, func(context.Context, api.Request) (*api.Document, error) { return nil, denial })
		if !errors.Is(err, denial) {
			t.Fatal("policy denial became successful inventory", err)
		}
	}
}

func TestAPIReplayConversationPanicClosesCredentialScope(t *testing.T) {
	var retained api.Fetch
	task, _ := newAPIReplayTask("https://example.com/careers", replayControllerMetadata, func(_ context.Context, fetch api.Fetch) error { retained = fetch; panic("fixture") })
	headers := http.Header{"Authorization": {"private"}}
	func() {
		defer func() {
			if recover() == nil {
				t.Fatal("fixture did not panic")
			}
		}()
		_ = converseAPIReplay(context.Background(), task.APIReplay, headers, nil, func(context.Context, api.Request) (*api.Document, error) {
			t.Fatal("unexpected request")
			return nil, nil
		})
	}()
	if len(headers) != 0 {
		t.Fatal("panic retained credentials")
	}
	if _, err := retained(context.Background(), api.Request{Method: "POST", URL: "https://example.com/api"}); !errors.Is(err, errReplayCapture) {
		t.Fatal("panic retained live fetch", err)
	}
}

func TestAPIReplayTaskRequiresSettledConversationAndExclusiveTarget(t *testing.T) {
	task, _ := newAPIReplayTask("https://example.com/careers", replayControllerMetadata, func(context.Context, api.Fetch) error { return nil })
	if validateResult(task, Result{}) == nil || validateResult(task, Result{apiReplaySessionSettled: true}) != nil {
		t.Fatal("unsettled target became a success")
	}
	task.Evaluation = &TaskEvaluation{Expression: "1", MaxResultBytes: 100}
	if validateTask(task) == nil {
		t.Fatal("arbitrary evaluation shared private replay target")
	}
}

func TestAPIReplayCaptureNeverExportsReflectedCredentialsOrRotatedQuery(t *testing.T) {
	c := replayTestCapture(t)
	defer c.erase()
	replayTestRequest(c, "one", "https://example.com/api?fresh=private-token")
	replayTestResponse(c, "one", network.Headers{})
	_, err := c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) {
		return []byte(`{"jobs":[{"id":"private-token"}]}`), nil
	})
	if !errors.Is(err, errReplayCapture) {
		t.Fatal("capture exported reflected credential", err)
	}
	c = replayTestCapture(t)
	defer c.erase()
	replayTestRequest(c, "one", "https://example.com/api?fresh=private-token")
	replayTestResponse(c, "one", network.Headers{"tdm-reservation": "1"})
	_, err = c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) { t.Fatal("denied body read"); return nil, nil })
	var reservation *policy.Reservation
	if !errors.As(err, &reservation) || reservation.URL != "https://example.com/api?stored=1" {
		t.Fatal("publisher evidence exported rotated URL", err)
	}
}
