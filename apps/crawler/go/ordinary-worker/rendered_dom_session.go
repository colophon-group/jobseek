package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"

	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func (r *NativeRenderedDetails) fetchHeldDOMPagination(ctx context.Context, profile queue.GreenhouseMonitorProfile, config map[string]string, options map[string]any) (RichDiscovery, error) {
	listing, _, err := queue.RenderedDOMMonitorOptions(config)
	if err != nil || listing.Pagination == nil || !queue.DOMMonitorUsesHeldPagination(config) {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	current, err := queue.InspectRichMonitor(profile.BoardID, config)
	if err != nil || current.Profile != profile.Profile || current.Endpoint != profile.Endpoint || current.EffectiveConfigSHA256 != profile.EffectiveConfigSHA256 || queue.MonitorWorker(current) != queue.Browser {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	wait, fallback, timeout := "networkidle", "domcontentloaded", uint64(30000)
	if v, ok := options["wait"].(string); ok {
		wait = v
	}
	var fallbackWait *string = &fallback
	if v, present := options["wait_fallback"]; present {
		if v == nil {
			fallbackWait = nil
		} else {
			fallback = v.(string)
		}
	}
	if fallbackWait != nil && *fallbackWait == wait {
		fallbackWait = nil
	}
	if v, ok := options["timeout"].(float64); ok {
		timeout = uint64(v)
	}
	for attempt := 0; attempt < 2; attempt++ {
		p := listing.Pagination
		hash := sha256.Sum256([]byte(profile.BoardID + "|" + profile.EffectiveConfigSHA256 + fmt.Sprint(attempt)))
		request := feed.Request{Protocol: feed.Protocol, RequestID: hex.EncodeToString(hash[:]), ConfigFingerprint: profile.EffectiveConfigSHA256, FeedURL: profile.Endpoint, Start: 1, Increment: 1, MaxPages: p.MaxPages, Wait: wait, WaitFallback: fallbackWait, NavigationTimeoutMS: timeout, TimeoutMS: feed.MaxDurationMS, DOM: &feed.DOMTraversal{Parameter: p.Param, PageStart: p.Start, Increment: p.Increment, Attempts: p.Attempts, Transient403: p.Transient403}}
		if !request.Valid() {
			return RichDiscovery{}, queue.ErrConfiguration
		}
		held, e := waitRenderedReservation(ctx, r.client.Reserve)
		if e != nil {
			return RichDiscovery{}, e
		}
		session, e := held.StartFeed(ctx, request)
		if e != nil {
			held.Close()
			return RichDiscovery{}, e
		}
		page := 0
		found, failure := collectDOMListingPages(ctx, profile, config, listing, func(current queue.GreenhouseMonitorProfile, c dom.ListingConfig, later bool) (RichDiscovery, error) {
			page++
			expected, e := request.PageURL(page)
			if e != nil || expected != current.Endpoint {
				return RichDiscovery{}, queue.ErrConfiguration
			}
			body, e := session.Page(page)
			if e != nil {
				return RichDiscovery{}, e
			}
			return parseHeldDOMPage(ctx, current, c, body, later)
		})
		if failure != nil {
			_ = session.Finish(false)
			session.Close()
			if page == 1 && attempt == 0 && errors.Is(failure, executor.ErrBotChallenge) {
				continue
			}
			return found, failure
		}
		if e = session.Finish(true); e != nil {
			session.Close()
			return RichDiscovery{Response: found.Response}, e
		}
		session.Close()
		return found, nil
	}
	return RichDiscovery{}, executor.ErrBotChallenge
}

func parseHeldDOMPage(ctx context.Context, profile queue.GreenhouseMonitorProfile, c dom.ListingConfig, body []byte, later bool) (RichDiscovery, error) {
	result, err := executor.DecodeResult(body)
	if err != nil || result.GetSuccess() == nil {
		return RichDiscovery{}, executor.ErrRenderedResult
	}
	s := result.GetSuccess()
	if s.ResourcePolicy == nil || len(s.Captures) != 1 || s.Captures[0].CaptureId != "dom-page" || len(s.Evaluations) != 0 || len(s.ActionOutcomes) != 0 || len(s.Artifacts) != 0 {
		return RichDiscovery{}, executor.ErrRenderedResult
	}
	if html, e := capturedFeedBytes(s.Html); e != nil || len(html) != 0 {
		return RichDiscovery{}, executor.ErrRenderedResult
	}
	source, err := capturedFeedBytes(s.Captures[0].Body)
	if err != nil {
		return RichDiscovery{}, err
	}
	u, e1 := url.Parse(s.FinalUrl)
	base, e2 := url.Parse(profile.Endpoint)
	if e1 != nil || e2 != nil || u.Scheme != base.Scheme || u.Host != base.Host || u.User != nil {
		return RichDiscovery{}, executor.ErrRenderedResult
	}
	response := &GreenhouseResponse{endpoint: profile.Endpoint, finalURL: s.FinalUrl, status: int(s.GetStatus()), bytes: len(source)}
	observation := httpObservation(ctx)
	observation.noteRequest(base.Hostname())
	observation.noteResponse(u.Hostname(), response.status)
	observation.noteBytes(len(source))
	found := RichDiscovery{Jobs: []RichMonitorJob{}, Response: response}
	if err = policy.Check(s.ResourcePolicy, string(source), s.FinalUrl); err != nil {
		var reservation *policy.Reservation
		if errors.As(err, &reservation) {
			response.reserved = true
			response.reservationSource, response.policy = reservation.Source, reservation.PolicyURL
		}
		return found, err
	}
	status := int(s.GetStatus())
	classification, classifyErr := dom.ClassifyDocument(string(source), dom.Object{}, profile.Endpoint)
	if classifyErr != nil {
		return found, classifyErr
	}
	if classification["classification"] == "challenge" {
		return found, executor.ErrBotChallenge
	}
	retryable := status >= 500 || status == 408 || status == 425 || status == 429 || c.Pagination != nil && c.Pagination.Transient403 && (status == 401 || status == 403)
	if later && status != 200 && !retryable {
		return found, nil
	}
	if status != 200 || len(source) == 0 {
		return found, &DiscoveryError{Kind: "inventory_failed"}
	}
	preview := string(source)
	if later {
		if chars := []rune(preview); len(chars) > 500000 {
			preview = string(chars[:500000])
		}
	}
	return parseDOMInventory(ctx, found, profile, c, preview, s.FinalUrl, !later, later)
}
