package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/url"
	"strings"
)

func (r *NativeRenderedDetails) fetchRSS(ctx context.Context, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	empty := RichDiscovery{Jobs: []RichMonitorJob{}}
	preset, p, options, e := queue.RSSOptions(config)
	if e != nil || !queue.RSSRenderedProfile(profile.Profile) {
		return empty, queue.ErrConfiguration
	}
	select {
	case r.slots <- struct{}{}:
		defer func() { <-r.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	wait, fallback := "networkidle", "domcontentloaded"
	timeout := uint64(30000)
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
	start, increment, maxPages, param := 1, 1, 1, ""
	if p != nil {
		start, increment, maxPages, param = p.Start, p.Increment, p.MaxPages, p.Param
		if maxPages == 0 {
			maxPages = 50001
		}
	}
	hash := sha256.Sum256([]byte(profile.BoardID + "|" + profile.EffectiveConfigSHA256 + "|" + profile.Endpoint))
	request := feed.Request{Protocol: feed.Protocol, RequestID: hex.EncodeToString(hash[:]), ConfigFingerprint: profile.EffectiveConfigSHA256, FeedURL: profile.Endpoint, PageParameter: param, Start: start, Increment: increment, MaxPages: maxPages, Wait: wait, WaitFallback: fallbackWait, NavigationTimeoutMS: timeout, TimeoutMS: feed.MaxDurationMS}
	if !request.Valid() {
		return empty, queue.ErrConfiguration
	}
	held, e := waitRenderedReservation(ctx, r.client.Reserve)
	if e != nil {
		return empty, e
	}
	session, e := held.StartFeed(ctx, request)
	if e != nil {
		held.Close()
		return empty, e
	}
	defer session.Close()
	page := start
	found, e := collectRSSPages(ctx, profile.Endpoint, p, true, func(ctx context.Context, endpoint string) (RichDiscovery, error) {
		expected, err := request.PageURL(page)
		if err != nil || expected != endpoint {
			return empty, queue.ErrConfiguration
		}
		payload, err := session.Page(page)
		page += increment
		if err != nil {
			return empty, err
		}
		result, err := executor.DecodeResult(payload)
		if err != nil {
			return empty, err
		}
		return parseRenderedRSSPage(ctx, endpoint, result, preset, strings.Contains(profile.Profile, "generic-summary"))
	})
	if e != nil {
		_ = session.Finish(false)
		return found, e
	}
	if e = session.Finish(true); e != nil {
		return RichDiscovery{Response: found.Response}, e
	}
	return found, nil
}

func parseRenderedRSSPage(ctx context.Context, endpoint string, result *runtimev1.BrowserResult, preset string, summary bool) (RichDiscovery, error) {
	found := RichDiscovery{Jobs: []RichMonitorJob{}}
	if result == nil || result.GetSuccess() == nil {
		return found, executor.ErrRenderedResult
	}
	success := result.GetSuccess()
	if success.Status == nil || success.GetStatus() < 100 || success.GetStatus() > 599 || success.Html == nil || success.Html.TotalSizeBytes != 0 || len(success.ActionOutcomes) != 0 || len(success.Captures) != 1 || len(success.Evaluations) != 0 || len(success.Artifacts) != 0 {
		return found, executor.ErrRenderedResult
	}
	if _, e := capturedFeedBytes(success.Html); e != nil {
		return found, e
	}
	u, e := url.Parse(success.FinalUrl)
	if e != nil || u.Hostname() == "" || u.User != nil || u.Fragment != "" || (u.Scheme != "https" && u.Scheme != "http") {
		return found, executor.ErrRenderedResult
	}
	capture := success.Captures[0]
	if capture == nil || capture.CaptureId != "feed" {
		return found, executor.ErrRenderedResult
	}
	body, e := capturedFeedBytes(capture.Body)
	if e != nil {
		return found, e
	}
	observed := &GreenhouseResponse{endpoint: endpoint, finalURL: success.FinalUrl, status: int(success.GetStatus()), bytes: len(body)}
	found.Response = observed
	requested, _ := url.Parse(endpoint)
	observation := httpObservation(ctx)
	observation.noteRequest(requested.Hostname())
	observation.noteResponse(u.Hostname(), observed.status)
	observation.noteBytes(len(body))
	// Match Python's raw XML sniff before publisher inspection; no DOM viewer.
	head := strings.ToLower(strings.TrimLeft(string(body[:min(len(body), 512)]), "\ufeff \t\r\n"))
	if !strings.HasPrefix(head, "<?xml") && !strings.HasPrefix(head, "<rss") && !strings.HasPrefix(head, "<feed") {
		return found, errors.New("RSS non-XML browser response")
	}
	if e = policy.Check(success.ResourcePolicy, string(body[:min(len(body), 512)]), endpoint); e != nil {
		var reservation *policy.Reservation
		if errors.As(e, &reservation) {
			observed.reserved = true
			observed.policy = reservation.PolicyURL
			observed.reservationSource = reservation.Source
		}
		return found, e
	}
	var parsed RichDiscovery
	if summary {
		parsed, e = parseGenericSummaryPage(body, false)
	} else {
		parsed, e = parseRSSProviderPage(body, "generic", true, false)
	}
	parsed.Response = observed
	return parsed, e
}
func capturedFeedBytes(manifest *runtimev1.ChunkManifest) ([]byte, error) {
	if manifest == nil || !manifest.Complete || manifest.TotalSizeBytes > feed.BodyLimit || len(manifest.TotalSha256) != 64 || uint64(len(manifest.Chunks)) != (manifest.TotalSizeBytes+65535)/65536 {
		return nil, executor.ErrRenderedResult
	}
	out := make([]byte, 0, int(manifest.TotalSizeBytes))
	for sequence, chunk := range manifest.Chunks {
		if chunk == nil || chunk.Sequence != uint32(sequence) {
			return nil, executor.ErrRenderedResult
		}
		inline, ok := chunk.Storage.(*runtimev1.DataChunk_InlineBody)
		if !ok || inline == nil {
			return nil, executor.ErrRenderedResult
		}
		expected := min(uint64(65536), manifest.TotalSizeBytes-uint64(len(out)))
		sum := sha256.Sum256(inline.InlineBody)
		if uint64(len(inline.InlineBody)) != expected || chunk.SizeBytes != expected || chunk.Sha256 != hex.EncodeToString(sum[:]) {
			return nil, executor.ErrRenderedResult
		}
		out = append(out, inline.InlineBody...)
	}
	sum := sha256.Sum256(out)
	if uint64(len(out)) != manifest.TotalSizeBytes || manifest.TotalSha256 != hex.EncodeToString(sum[:]) {
		return nil, executor.ErrRenderedResult
	}
	return out, nil
}
