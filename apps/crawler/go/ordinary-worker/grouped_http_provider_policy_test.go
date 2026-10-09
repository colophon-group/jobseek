package worker

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestGroupedHTTPProvidersPublisherRetryStatusAndCancellation(t *testing.T) {
	for _, c := range groupedHTTPInventoryCases(t) {
		if c.Mode != "rich" {
			continue
		}
		for _, mode := range []string{"header-reserved-404", "header-reserved-503", "body-reserved-503", "foreign-redirect", "transient", "malformed", "empty", "gone", "canceled"} {
			t.Run(c.Provider+"/"+mode, func(t *testing.T) {
				config, p, _ := groupedHTTPFixtureProfile(t, c)
				calls, waits := 0, 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					w.Header().Set("Content-Type", "application/json")
					switch mode {
					case "header-reserved-404", "header-reserved-503":
						w.Header().Set("TDM-Reservation", "1")
						if mode == "header-reserved-404" {
							w.WriteHeader(404)
						} else {
							w.WriteHeader(503)
						}
						return
					case "body-reserved-503":
						w.Header().Set("Content-Type", "text/html")
						w.WriteHeader(503)
						fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
						return
					case "foreign-redirect":
						http.Redirect(w, r, "https://foreign.example/private", 302)
						return
					case "transient":
						if calls == 1 {
							w.WriteHeader(503)
							return
						}
					case "malformed":
						fmt.Fprint(w, "malformed")
						return
					case "empty":
						return
					case "gone":
						w.WriteHeader(404)
						return
					}
					fmt.Fprint(w, c.Exchanges[0].Body)
				}))
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				if mode == "canceled" {
					cancel()
				}
				out, e := FetchFinalHTTPProvidersHTTP(ctx, client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
				if strings.Contains(mode, "reserved") {
					var reserved *policy.Reservation
					if !errors.As(e, &reserved) || calls != 1 || waits != 0 || out.Response == nil || !out.Response.reserved {
						t.Fatal("publisher precedence/evidence lost", e, calls, waits)
					}
					return
				}
				if mode == "canceled" {
					if !errors.Is(e, context.Canceled) || calls != 0 || len(out.Jobs) != 0 {
						t.Fatal("cancellation made progress", e, calls)
					}
					return
				}
				if mode == "transient" {
					if e != nil || calls != 2 || waits != 1 || len(out.Jobs) != 1 {
						t.Fatal("transient contract changed", e, calls, waits)
					}
					return
				}
				if e == nil || len(out.Jobs) != 0 {
					t.Fatal("failed inventory yielded prefix", e)
				}
				expected := 1
				if mode == "empty" || mode == "malformed" && c.Provider != "jobconvo" {
					expected = 3
				}
				if calls != expected || waits != expected-1 {
					t.Fatal("original retry contract changed", calls, waits, expected)
				}
			})
		}
	}
}

func TestGroupedHTTPProvidersLaterPageReservationDiscardsPrefix(t *testing.T) {
	for _, c := range groupedHTTPInventoryCases(t) {
		if c.Mode != "paged" {
			continue
		}
		config, p, _ := groupedHTTPFixtureProfile(t, c)
		calls := 0
		client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			calls++
			if calls == 2 {
				w.Header().Set("TDM-Reservation", "1")
				w.WriteHeader(503)
				return
			}
			fmt.Fprint(w, c.Exchanges[0].Body)
		}))
		out, e := FetchFinalHTTPProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
		var reserved *policy.Reservation
		if !errors.As(e, &reserved) || calls != 2 || len(out.Jobs) != 0 || out.Response == nil || !out.Response.reserved {
			t.Fatal("later page reservation yielded prefix", c.Provider, e, calls)
		}
	}
}

func TestJobConvoFirstPartyDefaultPortRedirect(t *testing.T) {
	for _, c := range groupedHTTPInventoryCases(t) {
		if c.Provider != "jobconvo" || c.Mode != "rich" {
			continue
		}
		config, p, _ := groupedHTTPFixtureProfile(t, c)
		calls := 0
		client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			calls++
			if calls == 1 {
				http.Redirect(w, r, "https://app.jobconvo.com:443"+r.URL.RequestURI(), 301)
				return
			}
			if r.Host != "app.jobconvo.com" {
				t.Error("default port was not normalized like original HTTPX")
			}
			fmt.Fprint(w, c.Exchanges[0].Body)
		}))
		out, e := FetchFinalHTTPProvidersHTTP(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
		if e != nil || calls != 2 || len(out.Jobs) != 1 {
			t.Fatal("first-party redirect contract changed", e, calls)
		}
	}
}
