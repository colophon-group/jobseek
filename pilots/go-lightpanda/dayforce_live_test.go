//go:build !densitybench

package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/csv"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	df "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/dayforcesession"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

// Explicit, read-only qualification of public registry boards. No production
// credentials, queues or persistence are available in this test container.
func TestLightpandaDayforceLiveRegistryQualification(t *testing.T) {
	path := os.Getenv("LIGHTPANDA_DAYFORCE_LIVE_REGISTRY")
	if path == "" {
		t.Skip("public registry qualification requires explicit opt-in")
	}
	binary := integrationBinary(t)
	f, err := os.Open(path)
	if err != nil {
		t.Fatal("public registry unavailable")
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil || len(rows) < 2 {
		t.Fatal("public registry invalid")
	}
	columns := map[string]int{}
	for i, name := range rows[0] {
		columns[name] = i
	}
	for _, name := range []string{"board_slug", "board_url", "monitor_type", "monitor_config"} {
		if _, ok := columns[name]; !ok {
			t.Fatal("public registry missing column")
		}
	}
	count := 0
	for _, row := range rows[1:] {
		if row[columns["monitor_type"]] != "dayforce" {
			continue
		}
		count++
		t.Run(row[columns["board_slug"]], func(t *testing.T) {
			metadata := row[columns["monitor_config"]]
			if strings.TrimSpace(metadata) == "" {
				metadata = "{}" // Registry sync compiles an absent config to an object.
			}
			board, overlap, err := api.DayforceOptionsFromMetadata(row[columns["board_url"]], metadata)
			if err != nil {
				t.Fatal("registry options invalid")
			}
			ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
			defer cancel()
			transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
			defer transport.CloseIdleConnections()
			client := &http.Client{Transport: transport, CheckRedirect: func(next *http.Request, prior []*http.Request) error {
				if len(prior) != 1 {
					return errors.New("unexpected listing redirect count")
				}
				allowed, e := board.LocalizedRedirect(prior[0].URL.String(), next.URL.String())
				if e != nil || allowed != next.URL.String() {
					return errors.New("unexpected listing redirect scope")
				}
				return nil
			}}
			request, _ := http.NewRequestWithContext(ctx, "GET", board.ListingURL(), nil)
			response, err := client.Do(request)
			if err != nil {
				t.Fatal("verified public HTTP bootstrap failed")
			}
			body, err := io.ReadAll(io.LimitReader(response.Body, 1_000_001))
			response.Body.Close()
			if err != nil || len(body) > 1_000_000 || response.StatusCode != 200 {
				t.Fatalf("public bootstrap status=%d bytes=%d", response.StatusCode, len(body))
			}
			signals := &runtimev1.ResourcePolicySignals{}
			if values := response.Header.Values("Tdm-Reservation"); len(values) > 0 {
				v := values[0]
				signals.TdmReservationHeader = &v
			}
			if values := response.Header.Values("Tdm-Policy"); len(values) > 0 {
				v := values[0]
				signals.TdmPolicyHeader = &v
			}
			if err = policy.Check(signals, string(body), response.Request.URL.String()); err != nil {
				t.Fatal("public publisher policy refuses qualification")
			}
			site, err := api.DayforceExtractSite(string(body), board)
			if err != nil || site.Disabled {
				t.Fatal("public site identity unavailable")
			}
			hash := sha256.Sum256([]byte(board.ListingURL()))
			identity := hex.EncodeToString(hash[:])
			wire := df.Request{Protocol: df.Protocol, RequestID: identity, ConfigFingerprint: identity, TargetURL: board.ListingURL(), Tenant: board.Tenant, Portal: board.Portal, ExpectedSite: df.Site{JobBoardID: site.JobBoardID, Culture: site.Culture, Cultures: site.Cultures}, OffsetOverlap: overlap, TimeoutMS: 90000}
			if !wire.Valid() {
				t.Fatal("public compiled request invalid")
			}
			pages, maxBytes := 0, 0
			conversation := func(ctx context.Context, ready df.Ready, fetch dayforceFetch) error {
				if ready.Status != 200 || !ready.PublisherChecked || ready.Reservation != nil || !reflect.DeepEqual(ready.Site, wire.ExpectedSite) {
					return errors.New("public browser bootstrap differs")
				}
				for _, offset := range []int{0, api.DayforcePageSize - overlap} {
					page, e := fetch(ctx, offset)
					if e != nil || page.TransportFailed || page.Status != 200 || page.FinalURL != board.SearchURL() {
						return errors.New("public browser search failed")
					}
					if e = policy.Check(page.Policy, string(page.Body), page.FinalURL); e != nil {
						return errors.New("public search policy refuses qualification")
					}
					document, e := api.Decode(page.Body)
					if e != nil {
						return errors.New("public search JSON invalid")
					}
					total, jobs, e := api.DayforcePage(document, board, site, offset)
					if e != nil {
						return errors.New("public search inventory invalid")
					}
					for _, job := range jobs {
						fields := api.DayforceJobFields(job, board, site)
						if fields == nil || fields["title"] == "" || fields["description"] == nil {
							return errors.New("public rich content unavailable")
						}
					}
					pages++
					maxBytes = max(maxBytes, len(page.Body))
					if total <= int64(offset+len(jobs)) {
						break
					}
				}
				return nil
			}
			_, err = runTask(ctx, Config{Binary: binary, EgressPolicy: defaultEgressPolicy(), TaskTimeout: 90 * time.Second}, Task{URL: wire.TargetURL, Navigation: &navigationOptions{wait: runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED, timeout: 60 * time.Second}, Dayforce: &dayforceTask{request: wire, converse: conversation}})
			if err != nil {
				t.Fatal("installed public browser qualification failed", err)
			}
			t.Logf("site=%d culture=%s bootstrap_bytes=%d pages=%d max_search_bytes=%d", site.JobBoardID, site.Culture, len(body), pages, maxBytes)
		})
	}
	if count != 10 {
		t.Fatalf("registry coverage changed: %d Dayforce boards", count)
	}
}
