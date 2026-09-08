package duefeeder

import (
	"math"
	"testing"

	queuev2 "github.com/colophon-group/jobseek/apps/crawler/contracts/queue/v2/conformance/go"
)

func TestReadyScoreAndAggregateCaps(t *testing.T) {
	nowMS := int64(1_000)
	for _, score := range []float64{1, 999, 1_000} {
		if !validReadyScore(score, nowMS) {
			t.Fatalf("valid score %v rejected", score)
		}
	}
	for _, score := range []float64{
		0, -1, 1_001, 1.5, math.NaN(), math.Inf(1),
		float64(queuev2.RedisCandidateMaxInteger + 1),
	} {
		if validReadyScore(score, nowMS) {
			t.Fatalf("invalid score %v accepted", score)
		}
	}
	if total, ok := consumeBytes(maxAggregateBytes-1, 1, maxAggregateBytes); !ok || total != maxAggregateBytes {
		t.Fatalf("boundary total=%d ok=%v", total, ok)
	}
	for _, test := range []struct {
		current  int
		addition int
		maximum  int
	}{
		{current: maxAggregateBytes, addition: 1, maximum: maxAggregateBytes},
		{current: -1, addition: 1, maximum: maxAggregateBytes},
		{current: 0, addition: -1, maximum: maxAggregateBytes},
		{current: 0, addition: 1, maximum: -1},
	} {
		if _, ok := consumeBytes(test.current, test.addition, test.maximum); ok {
			t.Fatalf("invalid budget accepted: %+v", test)
		}
	}
}
