package main

import (
	"bytes"
	"context"
	"encoding/json"
	"sort"
	"unicode/utf8"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
)

// Keep the original strict producer response surface, including capacity and
// unknown-decision refusal, for manifest, preparation and activation.
func decodeB0CutoverProducer(payload []byte) (capacityProducerDecision, error) {
	d := capacityProducerDecision{}
	if json.Unmarshal(payload, &d) != nil {
		return d, errB0Cutover
	}
	canonical, err := b0task.CanonicalJSON(d, true)
	if err != nil || !bytes.Equal(canonical, payload) || d.Version != capacityProducerProtocol || d.BoardSlugs == nil || d.LifetimeCapacity != 2048 || d.LifetimeOccupancy < 0 || d.LifetimeOccupancy > 2048 || d.LifetimeHeadroom != 2048-d.LifetimeOccupancy {
		return d, errB0Cutover
	}
	for _, slug := range d.BoardSlugs {
		if len(slug) == 0 || utf8.RuneCountInString(slug) > 128 {
			return d, errB0Cutover
		}
	}
	state := d.ExistingState == "" && d.ExistingPayloadSHA256 == "" || (d.ExistingState == "ready" || d.ExistingState == "inflight" || d.ExistingState == "dead" || d.ExistingState == "terminal") && capacityDigest.MatchString(d.ExistingPayloadSHA256)
	empty := d.PreparationDigest == "" && d.PayloadSHA256 == "" && d.ExistingState == "" && d.ExistingPayloadSHA256 == ""
	switch d.Outcome {
	case "manifest":
		cohort := d.Cohort == "c1" || d.Cohort == "c2" || d.Cohort == "c3" || d.Cohort == "c4" || d.Cohort == "cdom"
		if !cohort || d.Reason != "manifest" || d.Activated || !empty || len(d.BoardSlugs) == 0 || !sort.StringsAreSorted(d.BoardSlugs) {
			return d, errB0Cutover
		}
		for i := 1; i < len(d.BoardSlugs); i++ {
			if d.BoardSlugs[i] == d.BoardSlugs[i-1] {
				return d, errB0Cutover
			}
		}
	case "prepared", "activated":
		added := d.Reason == "activated" || d.Reason == "reactivated"
		reason := d.Outcome == "prepared" && d.Reason == "prepared" || d.Outcome == "activated" && (added || d.Reason == "already_activated")
		if !reason || !capacityDigest.MatchString(d.PreparationDigest) || !capacityDigest.MatchString(d.PayloadSHA256) || !state || d.Activated != added || d.Cohort != "" || len(d.BoardSlugs) != 0 {
			return d, errB0Cutover
		}
	case "legacy":
		if d.Reason != "literal_legacy" || d.Activated || !empty || d.Cohort != "" || len(d.BoardSlugs) != 0 {
			return d, errB0Cutover
		}
	default:
		// Error and capacity decisions never authorize an administrative plan.
		return d, errB0Cutover
	}
	return d, nil
}

func (p capacityProducer) cutoverManifest(ctx context.Context, cohort string) (capacityProducerDecision, error) {
	if !(cohort == "c1" || cohort == "c2" || cohort == "c3" || cohort == "cdom") {
		return capacityProducerDecision{}, errB0Cutover
	}
	request := map[string]any{"browser": false, "cohort": cohort, "config": map[string]string{}, "domain": "", "expected_digest": "", "first_time": false, "next_scrape_at_ms": int64(0), "operation": "manifest", "operator_transfer": true, "posting_id": "", "version": capacityProducerProtocol}
	body, err := p.exchange(ctx, request)
	if err != nil {
		return capacityProducerDecision{}, errB0Cutover
	}
	d, err := decodeB0CutoverProducer(body)
	if err != nil || d.Outcome != "manifest" || d.Cohort != cohort {
		return capacityProducerDecision{}, errB0Cutover
	}
	return d, nil
}

func (p capacityProducer) cutoverTask(ctx context.Context, operation, domain, posting string, score float64, config map[string]string, firstTime bool, sourceScore, expectedDigest string) (capacityProducerDecision, error) {
	if operation != "prepare" && operation != "activate" {
		return capacityProducerDecision{}, errB0Cutover
	}
	ms, err := capacityProducerMillis(score)
	if err != nil {
		return capacityProducerDecision{}, errB0Cutover
	}
	request := map[string]any{"browser": true, "cohort": "", "config": config, "domain": domain, "expected_digest": expectedDigest, "first_time": firstTime, "next_scrape_at_ms": ms, "operation": operation, "operator_transfer": true, "posting_id": posting, "version": capacityProducerProtocol}
	if sourceScore != "" {
		request["legacy_schedule_score"] = sourceScore
	}
	body, err := p.exchange(ctx, request)
	if err != nil {
		return capacityProducerDecision{}, errB0Cutover
	}
	d, err := decodeB0CutoverProducer(body)
	if err != nil || operation == "prepare" && d.Outcome != "prepared" || operation == "activate" && d.Outcome != "activated" {
		return capacityProducerDecision{}, errB0Cutover
	}
	return d, nil
}
