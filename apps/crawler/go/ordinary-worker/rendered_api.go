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
		title, err := executor.CoerceText(job.Title)
		if err != nil {
			return RichDiscovery{}, err
		}
		description, err := executor.CoerceText(job.Description)
		if err != nil {
			return RichDiscovery{}, err
		}
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: title, Description: description, Locations: job.Locations, Language: job.Metadata["language"], DatePosted: job.DatePosted, Metadata: job.Metadata, EmploymentType: job.EmploymentType, JobLocationType: job.JobLocationType, Extras: job.Extras})
	}
	return result, nil
}
