package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"math/rand/v2"
	"net/http"
	"reflect"
	"time"

	df "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/dayforcesession"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type dayforcePageSession interface {
	Search(int) (*df.Page, error)
	Finish(bool) error
	Close()
}

// FetchDayforceMonitor uses the verified Go HTTP bootstrap and one pinned
// browser reservation. This method alone grants no profile admission or write
// authority; the ordinary owner must hold and attest the compiled profile.
func (r *NativeRenderedDetails) FetchDayforceMonitor(ctx context.Context, p queue.GreenhouseMonitorProfile, config map[string]string, httpClient *http.Client) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	board, overlap, err := api.DayforceOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || r == nil || r.client == nil || httpClient == nil || config["monitor_needs_browser"] != "1" || p.Provider != "dayforce" || p.Profile != "dayforce.session-search/v1" || p.Endpoint != board.ListingURL() {
		return out, queue.ErrConfiguration
	}
	_, site, response, err := bootstrapDayforce(ctx, httpClient, board, pauseRich)
	out.Response = response
	if err != nil {
		return out, err
	}
	select {
	case r.slots <- struct{}{}:
		defer func() { <-r.slots }()
	case <-ctx.Done():
		return out, ctx.Err()
	}
	identity := sha256.Sum256([]byte(p.BoardID + "|" + p.EffectiveConfigSHA256 + "|" + p.Endpoint))
	request := df.Request{Protocol: df.Protocol, RequestID: hex.EncodeToString(identity[:]), ConfigFingerprint: p.EffectiveConfigSHA256, TargetURL: board.ListingURL(), Tenant: board.Tenant, Portal: board.Portal, ExpectedSite: dayforceWireSite(site), OffsetOverlap: overlap, TimeoutMS: df.MaxDurationMS}
	reservation, err := waitRenderedReservation(ctx, r.client.Reserve)
	if err != nil {
		return out, err
	}
	defer reservation.Close()
	session, ready, err := reservation.StartDayforce(ctx, request)
	if err != nil {
		return out, err
	}
	defer session.Close()
	complete := false
	defer func() {
		if !complete {
			_ = session.Finish(false)
		}
	}()
	response = &GreenhouseResponse{endpoint: board.ListingURL(), finalURL: ready.FinalURL, status: ready.Status}
	out.Response = response
	if !board.ResourceMatches(ready.FinalURL) || ready.FinalURL == board.SearchURL() || !ready.PublisherChecked {
		return out, queue.ErrConfiguration
	}
	if ready.Reservation != nil {
		response.reserved = true
		response.policy = ready.Reservation.PolicyURL
		response.reservationSource = ready.Reservation.Source
		return out, &policy.Reservation{URL: ready.FinalURL, Source: ready.Reservation.Source, PolicyURL: ready.Reservation.PolicyURL}
	}
	if ready.Status != 200 || !reflect.DeepEqual(ready.Site, request.ExpectedSite) {
		return out, &DiscoveryError{Kind: "dayforce_browser_bootstrap", Status: ready.Status}
	}
	out, err = discoverDayforcePages(ctx, board, site, overlap, func(ctx context.Context, offset int) (*api.Document, *GreenhouseResponse, error) {
		return fetchDayforceSessionPage(ctx, session, board.SearchURL(), offset, pauseRich)
	})
	if err != nil {
		return out, err
	}
	if err = session.Finish(true); err != nil {
		return RichDiscovery{Response: out.Response}, err
	}
	complete = true
	return out, nil
}

func dayforceWireSite(site api.DayforceSite) df.Site {
	return df.Site{JobBoardID: site.JobBoardID, Culture: site.Culture, Cultures: site.Cultures, Disabled: site.Disabled}
}

// Preserve actual Python api_sniff's three attempts, status-before-policy order,
// JSON parse retries and one-second exponential jitter. The caller's existing
// pagination core owns the distinct premature-empty retry and inventory checks.
func fetchDayforceSessionPage(ctx context.Context, session dayforcePageSession, source string, offset int, wait func(context.Context, time.Duration) error) (*api.Document, *GreenhouseResponse, error) {
	var response *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, response, err
		}
		page, err := session.Search(offset)
		if err != nil {
			return nil, response, err
		}
		if page == nil || page.Offset != offset || page.FinalURL != source {
			return nil, response, queue.ErrConfiguration
		}
		response = &GreenhouseResponse{endpoint: source, finalURL: source, status: page.Status, bytes: len(page.Body)}
		if !page.TransportFailed && page.Status >= 200 && page.Status < 300 {
			if page.Policy == nil {
				return nil, response, policy.ErrSignals
			}
			if err = policy.Check(page.Policy, string(page.Body), source); err != nil {
				var reservation *policy.Reservation
				if errors.As(err, &reservation) {
					response.reserved = true
					response.policy = reservation.PolicyURL
					response.reservationSource = reservation.Source
				}
				return nil, response, err
			}
			document, e := api.Decode(page.Body)
			if e == nil {
				return document, response, nil
			}
		} else if !page.TransportFailed && !(page.Status == 408 || page.Status == 425 || page.Status == 429 || page.Status >= 500 && page.Status <= 599) {
			return nil, response, &DiscoveryError{Kind: "dayforce_search_failed", Status: page.Status}
		}
		if attempt == 2 {
			return nil, response, &DiscoveryError{Kind: "dayforce_search_failed", Status: page.Status}
		}
		delay := time.Duration(float64(time.Second) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
		if err = wait(ctx, delay); err != nil {
			return nil, response, err
		}
	}
	return nil, response, api.ErrInventory
}
