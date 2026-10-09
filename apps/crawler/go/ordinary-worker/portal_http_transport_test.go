package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"

	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestPortalHTTPProvidersStatusRetryPublisherAndCancellation(t *testing.T) {
	for _, c := range portalInventoryCases(t) {
		if c.Name != "populated" && !(c.Provider == "infoniqa" && c.Name == "initial-jobs") {
			continue
		}
		for _, mode := range []string{"202", "401", "403", "404", "503", "429", "400", "empty", "invalid-document", "wrong-infoniqa-mime", "header-reserved", "body-reserved", "foreign-redirect", "cancelled"} {
			if mode == "wrong-infoniqa-mime" && c.Provider != "infoniqa" {
				continue
			}
			t.Run(c.Provider+"/"+mode, func(t *testing.T) {
				config, p := portalFixture(t, c)
				calls, waits := 0, 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					w.Header().Set("Content-Type", "text/html")
					if mode == "header-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "body-reserved" {
						fmt.Fprint(w, `<html><head><meta name="tdm-reservation" content="1"></head></html>`)
						return
					}
					if mode == "empty" {
						return
					}
					if mode == "invalid-document" {
						fmt.Fprint(w, "{")
						return
					}
					if mode == "wrong-infoniqa-mime" {
						w.Header().Set("Content-Type", "text/plain")
						fmt.Fprint(w, c.Exchanges[0].Response.Body)
						return
					}
					if mode == "foreign-redirect" {
						w.Header().Set("Location", "https://foreign.example/private")
						w.WriteHeader(302)
						return
					}
					status := 0
					fmt.Sscan(mode, &status)
					if status == 0 {
						t.Error("unexpected test request")
						status = 400
					}
					w.WriteHeader(status)
				}))
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				if mode == "cancelled" {
					cancel()
				}
				out, err := FetchPortalHTTPProviders(ctx, client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil }, nil)
				if err == nil || len(out.Jobs) != 0 {
					t.Fatal("unproved HTTP inventory accepted", err, len(out.Jobs))
				}
				want := 1
				if mode == "503" || mode == "429" || mode == "empty" || mode == "invalid-document" && c.Provider == "turbohire" || (mode == "401" || mode == "403") && c.Provider != "infoniqa" || mode == "202" && (c.Provider == "pageup" || c.Provider == "keka") {
					want = 3
				}
				if mode == "cancelled" {
					want = 0
					if !errors.Is(err, context.Canceled) {
						t.Fatal("cancellation cause lost", err)
					}
				}
				if calls != want || waits != max(0, want-1) {
					t.Fatal("original bounded retry decision changed", calls, waits, want)
				}
				var reserved *policy.Reservation
				if mode == "header-reserved" || mode == "body-reserved" {
					if !errors.As(err, &reserved) || out.Response == nil || !out.Response.reserved {
						t.Fatal("reservation lost before status or parse", err)
					}
				}
			})
		}
	}
}

func TestTurboHireChildConcurrencyReservationAndCancellationDrain(t *testing.T) {
	var original portalInventoryCase
	for _, c := range portalInventoryCases(t) {
		if c.Provider == "turbohire" && c.Name == "populated" {
			original = c
			break
		}
	}
	o, err := api.PortalHTTPProviderOptionsFromMetadata("turbohire", original.Board.URL, string(original.Board.Metadata))
	if err != nil {
		t.Fatal(err)
	}
	var detail map[string]any
	if json.Unmarshal([]byte(original.Exchanges[2].Response.Body), &detail) != nil {
		t.Fatal("original detail unavailable")
	}
	for _, mode := range []string{"complete", "reserved-child", "cancelled-parent"} {
		t.Run(mode, func(t *testing.T) {
			rows := []any{}
			for index := 0; index < 12; index++ {
				rows = append(rows, map[string]any{"JobId": fmt.Sprintf("job-%d", index), "JobIdObfuscated": fmt.Sprintf("public-%d", index)})
			}
			listing, _ := json.Marshal(map[string]any{"Total": 12, "Result": rows})
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			var active, maximum, started atomic.Int32
			var once sync.Once
			gate := make(chan struct{})
			fetch := func(child context.Context, r api.Request) ([]byte, error) {
				u, _ := url.Parse(r.URL)
				switch u.Path {
				case "/api/token/noauth":
					return []byte(`{"access_token":"public-token"}`), nil
				case "/api/careerpagev2/filteredjobs":
					return listing, nil
				}
				n := active.Add(1)
				defer active.Add(-1)
				for old := maximum.Load(); n > old; old = maximum.Load() {
					if maximum.CompareAndSwap(old, n) {
						break
					}
				}
				if started.Add(1) == 10 {
					once.Do(func() { close(gate) })
				}
				select {
				case <-gate:
				case <-child.Done():
					return nil, child.Err()
				}
				id := u.Query().Get("jobId")
				if mode == "reserved-child" {
					if id == "public-0" {
						return nil, &policy.Reservation{Source: "header"}
					}
					<-child.Done()
					return nil, child.Err()
				}
				if mode == "cancelled-parent" {
					cancel()
					<-child.Done()
					return nil, child.Err()
				}
				value := map[string]any{}
				for key, item := range detail {
					value[key] = item
				}
				value["JobId"] = "job-" + id[len("public-"):]
				value["JobIdObfuscated"] = id
				return json.Marshal(value)
			}
			jobs, err := api.DiscoverTurboHire(ctx, o, fetch, enrichment.NormalizeDescriptionHTML)
			if active.Load() != 0 || maximum.Load() > 10 || maximum.Load() != 10 {
				t.Fatal("child work escaped original ten-request batch or failed to drain", active.Load(), maximum.Load())
			}
			if mode == "complete" {
				if err != nil || len(jobs) != 12 || started.Load() != 12 {
					t.Fatal("complete bounded detail batch differs", err, len(jobs), started.Load())
				}
				return
			}
			if err == nil || len(jobs) != 0 || started.Load() != 10 {
				t.Fatal("failed child returned prefix or started another batch", err, len(jobs), started.Load())
			}
			var reserved *policy.Reservation
			if mode == "reserved-child" && !errors.As(err, &reserved) {
				t.Fatal("child reservation cause lost", err)
			}
			if mode == "cancelled-parent" && !errors.Is(err, context.Canceled) {
				t.Fatal("parent cancellation cause lost", err)
			}
		})
	}
}

func TestPortalKekaFirstForbiddenRedirectIsBoundedDisappearance(t *testing.T) {
	for _, c := range portalInventoryCases(t) {
		if c.Provider != "keka" || c.Name != "populated" {
			continue
		}
		config, p := portalFixture(t, c)
		calls := 0
		client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			calls++
			w.Header().Set("Location", "/careers/content/403.html")
			w.WriteHeader(302)
		}))
		out, err := FetchPortalHTTPProviders(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil }, nil)
		var failure *DiscoveryError
		if !errors.As(err, &failure) || failure.Kind != "provider_gone" || out.Response == nil || !out.Response.providerDisabled || calls != 1 {
			t.Fatal("original forbidden redirect no longer classified", err, calls)
		}
	}
}
