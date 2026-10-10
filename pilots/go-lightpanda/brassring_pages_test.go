package main

import (
	"context"
	"errors"
	"testing"

	"github.com/chromedp/cdproto/network"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestBrassRingCaptureRejectsStatusBeforeReadingBody(t *testing.T) {
	c := replayTestCapture(t)
	defer c.erase()
	c.requireStatus = 200
	replayTestRequest(c, "one", "https://example.com/api")
	c.observe(&network.EventResponseReceived{RequestID: "one", Type: network.ResourceTypeFetch, Response: &network.Response{URL: "https://example.com/api", Status: 503}})
	c.observe(&network.EventLoadingFinished{RequestID: "one", EncodedDataLength: 100})
	_, err := c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) {
		t.Fatal("failed HTTP response was parsed as an authoritative snapshot")
		return nil, nil
	})
	var status *replayStatusError
	if !errors.As(err, &status) || status.status != 503 {
		t.Fatal("original HTTP200 requirement lost", err)
	}
}

func TestBrassRingCapturePreservesEmptySnapshotAndPublisherReservation(t *testing.T) {
	for _, denied := range []bool{false, true} {
		c := replayTestCapture(t)
		c.requireStatus = 200
		replayTestRequest(c, "one", "https://example.com/api")
		headers := network.Headers{}
		if denied {
			headers["tdm-reservation"] = "1"
		}
		replayTestResponse(c, "one", headers)
		exchanges, err := c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) {
			if denied {
				t.Fatal("publisher reservation read response body")
			}
			return []byte(`{"JobsCount":0,"Jobs":{"Job":[]}}`), nil
		})
		if denied {
			var reserved *policy.Reservation
			if !errors.As(err, &reserved) {
				t.Fatal("publisher reservation became retry or empty result", err)
			}
		} else {
			h, document, matched, selected := api.SelectBrowserReplayExchange(api.BrowserReplayOptions{Inventory: api.Options{Endpoint: "https://example.com/api", Method: "POST"}}, exchanges)
			clear(h)
			if err != nil || selected != nil || !matched || document == nil {
				t.Fatal("original empty snapshot was lost", err, selected)
			}
			total, rows, parsed := api.BrassRingPage(document)
			if parsed != nil || total != 0 || len(rows) != 0 {
				t.Fatal("original empty snapshot changed", parsed)
			}
		}
		c.erase()
	}
}

func TestBrassRingBrowserPageRejectsUnsupportedActionsBeforeBrowserUse(t *testing.T) {
	board := "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=5998"
	for _, action := range []struct {
		page     int
		sorted   bool
		metadata string
	}{{0, false, `{}`}, {50001, false, `{}`}, {2, true, `{}`}, {1, false, `{"proxy":true}`}} {
		if _, err := loadBrassRingBrowserPage(context.Background(), board, action.metadata, action.page, action.sorted); err == nil {
			t.Fatal("unsupported browser action admitted")
		}
	}
}
