package worker

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func (r *NativeRenderedDetails) fetchAPIDetailCapture(ctx context.Context, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	metadata, err := json.Marshal(p.HTTPAPIConfig)
	if err != nil {
		return nil, nil, queue.ErrConfiguration
	}
	if _, err := api.BrowserDetailOptionsFromConfig(metadata); err != nil {
		return nil, nil, queue.ErrConfiguration
	}
	id := sha256.Sum256([]byte(p.BoardID + "|" + p.EffectiveBoardSHA256 + "|" + p.SourceURL))
	request := replay.Request{Protocol: replay.Protocol, RequestID: hex.EncodeToString(id[:]), ConfigFingerprint: p.EffectiveBoardSHA256, Operation: "detail", BoardURL: p.SourceURL, Metadata: metadata, TimeoutMS: replay.MaxDurationMS}
	if !request.Valid() {
		return nil, nil, queue.ErrConfiguration
	}
	held, err := waitRenderedReservation(ctx, r.client.Reserve)
	if err != nil {
		return nil, nil, err
	}
	response, err := held.APIReplay(ctx, request)
	if err != nil {
		return nil, nil, err
	}
	return parseAPIDetailCapture(p, response)
}

func parseAPIDetailCapture(p queue.WorkdayDetailProfile, response replay.Response) (map[string]any, *policy.Reservation, error) {
	if p.Profile != "api_sniffer.browser-detail/v1" || !response.Valid() || response.ConfigFingerprint != p.EffectiveBoardSHA256 || len(response.Inventory) != 0 {
		return nil, nil, executor.ErrRenderedResult
	}
	switch response.Outcome {
	case "publisher_reserved":
		return nil, &policy.Reservation{URL: response.Reservation.URL, Source: response.Reservation.Source, PolicyURL: response.Reservation.PolicyURL}, nil
	case "invalid_config":
		return nil, nil, queue.ErrConfiguration
	case "failed":
		return nil, nil, errors.New("API detail capture failed")
	case "success":
		if len(response.Detail) == 0 {
			return nil, nil, executor.ErrRenderedResult
		}
		decoder := json.NewDecoder(bytes.NewReader(response.Detail))
		decoder.UseNumber()
		var content map[string]any
		if decoder.Decode(&content) != nil || decoder.Decode(new(any)) != io.EOF || content == nil {
			return nil, nil, executor.ErrRenderedResult
		}
		return content, nil, nil
	default:
		return nil, nil, executor.ErrRenderedResult
	}
}
