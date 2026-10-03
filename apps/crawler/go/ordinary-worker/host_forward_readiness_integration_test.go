//go:build integration

package worker

import (
	"encoding/json"
	"testing"

	"github.com/redis/go-redis/v9"
)

func TestHostForwardReadinessConservesSharedDomains(t *testing.T) {
	snapshot := func(expiry string, rows ...redis.Z) string {
		for n := range rows {
			rows[n].Member = []byte(rows[n].Member.(string))
		}
		body, err := json.Marshal(rows)
		if err != nil {
			t.Fatal(err)
		}
		return "zset:" + expiry + ":" + string(body)
	}
	other := redis.Z{Member: "unrelated.example.test", Score: 12.125}
	owned := redis.Z{Member: "jobs.example.test", Score: 350.0001}
	before, _, err := hostForwardReadinessProjection(snapshot("false", other, owned))
	after, score, afterErr := hostForwardReadinessProjection(snapshot("false", other))
	if err != nil || afterErr != nil || before != after || score != nil {
		t.Fatal("owned removal must conserve unrelated members")
	}
	_, score, err = hostForwardReadinessProjection(snapshot("false", other, owned))
	if err != nil || score == nil || *score != owned.Score {
		t.Fatal("exact owned fractional score lost")
	}
	for _, changed := range []string{
		snapshot("false", redis.Z{Member: "unrelated.example.test", Score: 12.126}),
		snapshot("false", redis.Z{Member: "replacement.example.test", Score: 12.125}),
		snapshot("true", other),
		"",
	} {
		got, _, err := hostForwardReadinessProjection(changed)
		if err != nil || got == before {
			t.Fatal("unrelated member, score, expiry or removal drift escaped")
		}
	}
	got, score, err := hostForwardReadinessProjection(snapshot("false", owned))
	if err != nil || got != "" || score == nil {
		t.Fatal("owned-only index may disappear after transfer")
	}
	for _, malformed := range []string{"string:false:[]", "zset:unknown:[]", "zset:false:invalid", snapshot("false", owned, owned)} {
		if _, _, err := hostForwardReadinessProjection(malformed); err == nil {
			t.Fatal("malformed readiness observation admitted")
		}
	}
}
