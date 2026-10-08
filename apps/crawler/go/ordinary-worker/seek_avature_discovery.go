package worker

import (
	"context"
	"errors"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"time"
)

func FetchSeekAvatureHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	if client == nil || wait == nil || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	var seek api.SeekOptions
	var avature api.AvatureOptions
	var scope func(string) bool
	var err error
	switch p.Provider {
	case "seek":
		seek, err = api.SeekOptionsFromMetadata(config["board_url"], config["metadata"])
		scope = seek.ResourceMatches
		if err != nil || p.Profile != "seek.advertiser-urls/v1" || p.Endpoint != seek.PageURL(1) {
			return out, queue.ErrConfiguration
		}
	case "avature":
		avature, err = api.AvatureOptionsFromMetadata(config["board_url"], config["metadata"])
		scope = avature.ResourceMatches
		if err != nil || p.Profile != "avature.listing-urls/v1" || p.Endpoint != avature.Board.ListingURL() {
			return out, queue.ErrConfiguration
		}
	default:
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.Jar, err = cookiejar.New(nil)
	if err != nil {
		return out, err
	}
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(ctx context.Context, resource string) ([]byte, error) {
		if !scope(resource) {
			return nil, queue.ErrConfiguration
		}
		current := resource
		redirects := 0
		for attempt := 0; attempt < 3; {
			requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			request, e := http.NewRequestWithContext(requestCtx, http.MethodGet, current, nil)
			if e != nil {
				cancel()
				return nil, queue.ErrConfiguration
			}
			request.Header.Set("User-Agent", ordinaryUserAgent)
			request.Header.Set("Accept", ordinaryAccept)
			if p.Provider == "seek" {
				request.Header.Set("Accept", "application/json")
			}
			response, e := sealed.Do(request)
			status := 0
			var raw []byte
			readFailed := false
			if response != nil {
				observed := &GreenhouseResponse{endpoint: resource, finalURL: response.Request.URL.String(), status: response.StatusCode, location: response.Header.Get("Location"), contentType: response.Header.Get("Content-Type")}
				out.Response = observed
				status = response.StatusCode
				reservation, policyURL := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
				signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}
				check := func(body string) error {
					e := policy.Check(signals, body, observed.finalURL)
					var reserved *policy.Reservation
					if errors.As(e, &reserved) {
						observed.reserved, observed.policy, observed.reservationSource = true, reserved.PolicyURL, reserved.Source
					}
					return e
				}
				if e = check(""); e == nil {
					var readError error
					raw, readError = io.ReadAll(io.LimitReader(response.Body, 2_000_001))
					observed.bytes = len(raw)
					if len(raw) > 2_000_000 {
						e = api.ErrInventory
					} else {
						e = check(jsonld.DecodeDocument(raw, observed.contentType))
						if e == nil && readError != nil {
							e, readFailed = readError, true
						}
					}
				}
				response.Body.Close()
				cancel()
				if e != nil && !readFailed {
					return nil, e
				}
				if !readFailed && p.Provider == "avature" && resource == p.Endpoint && redirects < 5 && (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
					base, _ := url.Parse(current)
					ref, e := url.Parse(observed.location)
					if e != nil || observed.location == "" {
						return nil, queue.ErrConfiguration
					}
					next := base.ResolveReference(ref).String()
					if !scope(next) {
						return nil, queue.ErrConfiguration
					}
					current = next
					redirects++
					continue
				}
				if !readFailed && status == 200 && len(strings.TrimSpace(string(raw))) > 0 {
					media := strings.ToLower(strings.TrimSpace(strings.Split(observed.contentType, ";")[0]))
					if p.Provider == "seek" {
						if media != "application/json" && !strings.HasSuffix(media, "+json") {
							return nil, api.ErrInventory
						}
					} else {
						if media != "text/html" && media != "application/xhtml+xml" {
							return nil, api.ErrInventory
						}
						classification, e := dom.ClassifyDocument(jsonld.DecodeDocument(raw, observed.contentType), dom.Object{}, observed.finalURL)
						if e != nil || classification["classification"] == "challenge" {
							return nil, api.ErrInventory
						}
					}
					return []byte(jsonld.DecodeDocument(raw, observed.contentType)), nil
				}
			} else {
				cancel()
				if ctx.Err() != nil {
					return nil, ctx.Err()
				}
			}
			retry := readFailed || status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599 || status == 202 || status == 403 || p.Provider == "avature" && (status == 401 || status == 406)
			if !retry || attempt == 2 {
				if e != nil {
					return nil, e
				}
				return nil, &DiscoveryError{Kind: "provider_resource_failed", Status: status}
			}
			if err := wait(ctx, time.Duration(1<<attempt)*500*time.Millisecond); err != nil {
				return nil, err
			}
			attempt++
		}
		return nil, api.ErrInventory
	}
	var urls []string
	if p.Provider == "seek" {
		urls, err = api.DiscoverSeek(ctx, seek, fetch)
	} else {
		var inventory api.AvatureInventory
		inventory, err = api.DiscoverAvature(ctx, avature, fetch)
		urls, out.Truncated, out.MetadataUpdates = inventory.URLs, inventory.Truncated, inventory.Metadata
	}
	if err != nil {
		return out, err
	}
	for _, u := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: u, URLOnly: true})
	}
	return out, ctx.Err()
}
