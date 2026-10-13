package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	q "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"google.golang.org/protobuf/proto"
)

func (r *NativeRenderedDetails) fetchInlineCards(ctx context.Context, p q.GreenhouseMonitorProfile, config map[string]string, options map[string]any, cards *actions.InlineCards) (RichDiscovery, error) {
	empty := RichDiscovery{Jobs: []RichMonitorJob{}}
	digest := sha256.Sum256([]byte(p.BoardID + "|" + p.EffectiveConfigSHA256 + "|" + p.Endpoint))
	id := hex.EncodeToString(digest[:])
	wait := "domcontentloaded"
	if v, ok := options["wait"].(string); ok {
		wait = v
	}
	timeout := uint64(30000)
	if v, ok := options["timeout"].(float64); ok {
		timeout = uint64(v)
	}
	input, err := lp.NavigationInput(lp.Navigation{URL: p.Endpoint, RoutingRevision: p.EffectiveConfigSHA256, OriginRequestID: "ordinary-monitor:" + id, Wait: wait, TimeoutMS: timeout, TransportRetries: 1})
	if err != nil {
		return empty, err
	}
	payload, err := proto.Marshal(input)
	if err != nil {
		return empty, err
	}
	pipeline, err := actions.Parse(options["actions"])
	if err != nil {
		return empty, err
	}
	held, err := waitRenderedReservation(ctx, r.client.Reserve)
	if err != nil {
		return empty, err
	}
	defer held.Close()
	response, err := held.DocumentActions(ctx, actions.Request{Protocol: actions.Protocol, RequestID: id, ConfigFingerprint: p.EffectiveConfigSHA256, Input: payload, Actions: pipeline, InlineCards: cards})
	if err != nil {
		return empty, err
	}
	value, err := executor.DecodeResult(response.Result)
	if err != nil {
		return empty, err
	}
	if len(response.InlineIdentities) == 0 {
		found, e := parseHeldRenderedMonitorWithInlineIdentities(ctx, p, config, value, []string{})
		if found.Response != nil && found.Response.reserved {
			return found, e
		}
		return empty, actions.ErrActions
	}
	return parseHeldRenderedMonitorWithInlineIdentities(ctx, p, config, value, response.InlineIdentities)
}
