package worker

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func (r *NativeRenderedDetails) fetchAPIReplay(ctx context.Context, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	empty := RichDiscovery{Jobs: []RichMonitorJob{}}
	if _, err := queue.APISnifferBrowserMonitorOptions(config); err != nil || profile.Profile != "api_sniffer.browser-items/v1" || profile.Endpoint != config["board_url"] {
		return empty, queue.ErrConfiguration
	}
	select {
	case r.slots <- struct{}{}:
		defer func() { <-r.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	id := sha256.Sum256([]byte(profile.BoardID + "|" + profile.EffectiveConfigSHA256 + "|" + profile.Endpoint))
	request := replay.Request{Protocol: replay.Protocol, RequestID: hex.EncodeToString(id[:]), ConfigFingerprint: profile.EffectiveConfigSHA256, BoardURL: profile.Endpoint, Metadata: json.RawMessage(config["metadata"]), TimeoutMS: replay.MaxDurationMS}
	if !request.Valid() {
		return empty, queue.ErrConfiguration
	}
	held, err := waitRenderedReservation(ctx, r.client.Reserve)
	if err != nil {
		return empty, err
	}
	response, err := held.APIReplay(ctx, request)
	if err != nil {
		return empty, err
	}
	return parseAPIReplayResponse(profile, response)
}

func parseAPIReplayResponse(profile queue.GreenhouseMonitorProfile, response replay.Response) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if !response.Valid() || response.ConfigFingerprint != profile.EffectiveConfigSHA256 {
		return result, executor.ErrRenderedResult
	}
	if response.Outcome == "partial" && profile.Profile != "darwinbox.session-items/v1" {
		return result, executor.ErrRenderedResult
	}
	if response.Outcome == "provider_gone" {
		if profile.Profile != "darwinbox.session-items/v1" || !api.NativeBrowserResourceMatches(profile.Provider, profile.Endpoint, "{}", response.FailureURL) {
			return result, executor.ErrRenderedResult
		}
		result.Response = &GreenhouseResponse{endpoint: response.FailureURL, finalURL: response.FailureURL, status: response.FailureStatus}
		return result, &DiscoveryError{Kind: "provider_gone", Status: response.FailureStatus}
	}
	switch response.Outcome {
	case "publisher_reserved":
		result.Response = &GreenhouseResponse{endpoint: response.Reservation.URL, finalURL: response.Reservation.URL, reserved: true, policy: response.Reservation.PolicyURL, reservationSource: response.Reservation.Source}
		return result, &DiscoveryError{Kind: "publisher_reserved"}
	case "invalid_config":
		return result, queue.ErrConfiguration
	case "failed":
		return result, errors.New("API replay inventory failed")
	}
	var inventory api.Inventory
	decoder := json.NewDecoder(bytes.NewReader(response.Inventory))
	decoder.UseNumber()
	decoder.DisallowUnknownFields()
	if decoder.Decode(&inventory) != nil {
		return result, executor.ErrRenderedResult
	}
	result.Truncated = inventory.Truncated
	for _, job := range inventory.Jobs {
		if queue.NativeBrowserProfile(profile.Profile) {
			value, err := secondaryRichJob(map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "metadata": job.Metadata, "extras": job.Extras, "date_posted": job.DatePosted, "employment_type": job.EmploymentType, "job_location_type": job.JobLocationType})
			if err != nil {
				return RichDiscovery{}, err
			}
			result.Jobs = append(result.Jobs, value)
			continue
		}
		title, err := executor.CoerceText(job.Title)
		if err != nil {
			return RichDiscovery{}, err
		}
		description, err := executor.CoerceText(job.Description)
		if err != nil {
			return RichDiscovery{}, err
		}
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, URLOnly: inventory.URLOnly, Title: title, Description: description, Locations: job.Locations, Language: job.Metadata["language"], DatePosted: job.DatePosted, Metadata: job.Metadata, EmploymentType: job.EmploymentType, JobLocationType: job.JobLocationType, Extras: job.Extras})
	}
	if response.Outcome == "partial" {
		if len(result.Jobs) == 0 || result.Truncated {
			return RichDiscovery{}, executor.ErrRenderedResult
		}
		return result, &rssStreamPrefixError{cause: errors.New("Darwinbox later page failed")}
	}
	return result, nil
}

func (r *NativeRenderedDetails) fetchNativeBrowserProvider(ctx context.Context, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	empty := RichDiscovery{Jobs: []RichMonitorJob{}}
	_, listing, e := api.NativeBrowserOptions(profile.Provider, config["board_url"], config["metadata"])
	if e != nil || !queue.NativeBrowserProfile(profile.Profile) || listing != profile.Endpoint || profile.Provider != config["crawler_type"] {
		return empty, queue.ErrConfiguration
	}
	select {
	case r.slots <- struct{}{}:
		defer func() { <-r.slots }()
	case <-ctx.Done():
		return empty, ctx.Err()
	}
	id := sha256.Sum256([]byte(profile.BoardID + "|" + profile.EffectiveConfigSHA256 + "|" + listing))
	request := replay.Request{Protocol: replay.Protocol, RequestID: hex.EncodeToString(id[:]), ConfigFingerprint: profile.EffectiveConfigSHA256, BoardURL: listing, Metadata: json.RawMessage(config["metadata"]), Provider: profile.Provider, TimeoutMS: replay.MaxDurationMS}
	if !request.Valid() {
		return empty, queue.ErrConfiguration
	}
	held, e := waitRenderedReservation(ctx, r.client.Reserve)
	if e != nil {
		return empty, e
	}
	response, e := held.APIReplay(ctx, request)
	if e != nil {
		return empty, e
	}
	return parseAPIReplayResponse(profile, response)
}
