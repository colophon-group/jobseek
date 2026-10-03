package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"
)

func cleanupTestDigest(body []byte) string {
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}

func cleanupTestDecision() producerColdCleanupDecision {
	hash := strings.Repeat("a", 64)
	return producerColdCleanupDecision{hash, hash, hash, "c1", expectedLuaSHA256, "prod-b0", hash, 11, hash, "ghcr.io/colophon-group/jobseek-crawler@sha256:" + hash, producerColdCleanupSchema, "lightpanda-b0", 10, hash, strings.Repeat("b", 40), hash}
}

func TestColdCleanupDecisionBindsSourceAndHostEvidence(t *testing.T) {
	d := cleanupTestDecision()
	c := producerConfig{Cohort: d.Cohort, Namespace: d.Namespace, Route: routeIdentity{d.ShardID, d.SourceEpoch, engineOwner}}
	body, _ := json.Marshal(d)
	if _, err := decodeProducerColdCleanupDecision(body, cleanupTestDigest(body), c, d.SourceRevision); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*producerColdCleanupDecision){
		func(d *producerColdCleanupDecision) { d.SourceEpoch++ },
		func(d *producerColdCleanupDecision) { d.RetirementEpoch = d.SourceEpoch },
		func(d *producerColdCleanupDecision) { d.RetirementEpoch = maxInteger + 1 },
		func(d *producerColdCleanupDecision) { d.SourceRevision = strings.Repeat("c", 40) },
		func(d *producerColdCleanupDecision) { d.Cohort = "cdom" },
		func(d *producerColdCleanupDecision) { d.Namespace = "other" },
		func(d *producerColdCleanupDecision) { d.ShardID = "other" },
		func(d *producerColdCleanupDecision) { d.RuntimeImage = "latest" },
		func(d *producerColdCleanupDecision) { d.LuaSHA256 = strings.Repeat("c", 64) },
		func(d *producerColdCleanupDecision) { d.SQLCleanupReceiptSHA256 = "" },
		func(d *producerColdCleanupDecision) { d.AllWritersReceiptSHA256 = "" },
		func(d *producerColdCleanupDecision) { d.B0RestorationPlanSHA256 = "" },
		func(d *producerColdCleanupDecision) { d.SourceReceiptSHA256 = "" },
		func(d *producerColdCleanupDecision) { d.ActivationSentinelSHA256 = "" },
		func(d *producerColdCleanupDecision) { d.OrdinaryRestorationPlanSHA256 = "" },
		func(d *producerColdCleanupDecision) { d.ReversalSHA256 = "" },
	} {
		altered := d
		change(&altered)
		raw, _ := json.Marshal(altered)
		if _, err := decodeProducerColdCleanupDecision(raw, cleanupTestDigest(raw), c, d.SourceRevision); err == nil {
			t.Fatal("rehashed cleanup drift admitted")
		}
	}
	for _, raw := range [][]byte{append(append([]byte{}, body...), '\n'), []byte(strings.Replace(string(body), "{", "{\"extra\":1,", 1)), []byte(strings.Replace(string(body), "{", "{\"cohort\":\"c1\",", 1))} {
		if _, err := decodeProducerColdCleanupDecision(raw, cleanupTestDigest(raw), c, d.SourceRevision); err == nil {
			t.Fatal("noncanonical cleanup admitted")
		}
	}
	if _, err := decodeProducerColdCleanupDecision(body, strings.Repeat("c", 64), c, d.SourceRevision); err == nil {
		t.Fatal("wrong cleanup digest admitted")
	}
}

func TestColdCleanupModeCannotStartServingOrInitialization(t *testing.T) {
	for key, value := range map[string]string{
		"REDIS_URL": "unix:///tmp/unopened-cleanup-test.sock", "LIGHTPANDA_B0_ROUTING_EPOCH": "10",
		"LIGHTPANDA_B0_PRODUCER_CLIENT_UID": strconv.Itoa(os.Geteuid() + 1), "LIGHTPANDA_B0_QUEUE_NAMESPACE": "prod-b0",
		"LIGHTPANDA_B0_SHARD_ID": "lightpanda-b0", "LIGHTPANDA_B0_PRODUCER_COHORT": "c1", "LIGHTPANDA_B0_PRODUCER_SOCKET": producerSocketPath,
	} {
		t.Setenv(key, value)
	}
	t.Setenv("LIGHTPANDA_B0_PRODUCER_MODE", "off")
	if _, err := producerConfigForMode("off"); err != nil {
		t.Fatal("explicit cleanup mode refused", err)
	}
	if _, err := producerConfigFromEnvironment(); err == nil {
		t.Fatal("off mode accepted by serving configuration")
	}
	t.Setenv("LIGHTPANDA_B0_PRODUCER_MODE", modeEnabled)
	if _, err := producerConfigForMode("off"); err == nil {
		t.Fatal("enabled mode accepted by cleanup configuration")
	}
}
