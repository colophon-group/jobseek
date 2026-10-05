package worker

import (
	"context"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
)

func TestICIMSPageSuccessfulPolicyStopsBeforeBodyAndJSONByteBound(t *testing.T) {
	for _, mode := range []string{"header", "json_limit", "unicode_text"} {
		t.Run(mode, func(t *testing.T) {
			var calls atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if mode == "header" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(200)
					return
				}
				if mode == "json_limit" {
					w.Write([]byte(strings.Repeat("x", 2_000_001)))
					return
				}
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				w.Write([]byte(strings.Repeat("世", 2_000_001)))
			}))
			body, response, err := fetchICIMSPage(context.Background(), client.client, dom.ICIMSRequest{URL: "https://careers-native.icims.com/jobs/search", JSON: mode == "json_limit"})
			if calls.Load() != 1 || response == nil || response.status != 200 {
				t.Fatal("request bound changed", calls.Load(), response)
			}
			switch mode {
			case "header":
				if err == nil || !response.reserved || len(body) != 0 {
					t.Fatal("successful publisher header lost", response, err)
				}
			case "json_limit":
				if err == nil || len(body) != 0 || response.reserved {
					t.Fatal("oversized JSON accepted", err)
				}
			case "unicode_text":
				if err != nil || len([]rune(string(body))) != 2_000_000 {
					t.Fatal("text cap became a byte cap", len(body), err)
				}
			}
		})
	}
}
