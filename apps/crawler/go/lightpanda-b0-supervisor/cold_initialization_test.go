package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestColdProducerDecisionBindsExactProtectedRuntime(t *testing.T) {
	config := producerConfig{Cohort: "c1", Namespace: "prod-b0", Route: routeIdentity{"lightpanda-b0", 11, "go"}}
	d := producerColdDecision{strings.Repeat("a", 64), "c1", expectedLuaSHA256, "prod-b0", strings.Repeat("b", 64), strings.Repeat("c", 64), 11, "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("d", 64), producerColdSchema, "lightpanda-b0", 10, strings.Repeat("e", 40)}
	body, _ := json.Marshal(d)
	digest := func(b []byte) string { sum := sha256.Sum256(b); return hex.EncodeToString(sum[:]) }
	if err := decodeProducerColdDecision(body, digest(body), config, d.SourceRevision); err != nil {
		t.Fatal(err)
	}
	for _, alter := range []func(*producerColdDecision){
		func(d *producerColdDecision) { d.RoutingEpoch++ }, func(d *producerColdDecision) { d.Cohort = "cdom" },
		func(d *producerColdDecision) { d.SourceRevision = strings.Repeat("f", 40) }, func(d *producerColdDecision) { d.SourceEpoch = d.RoutingEpoch },
		func(d *producerColdDecision) { d.LuaSHA256 = strings.Repeat("f", 64) }, func(d *producerColdDecision) { d.RuntimeImage = "latest" },
		func(d *producerColdDecision) { d.OrdinaryRestorationPlanSHA256 = "" }, func(d *producerColdDecision) { d.AllWritersReceiptSHA256 = "" },
	} {
		changed := d
		alter(&changed)
		raw, _ := json.Marshal(changed)
		if decodeProducerColdDecision(raw, digest(raw), config, d.SourceRevision) == nil {
			t.Fatal("rehashed drift accepted")
		}
	}
	for _, raw := range [][]byte{append(body, '\n'), []byte(strings.Replace(string(body), "{", "{\"extra\":true,", 1)), []byte(strings.Replace(string(body), "{", "{\"cohort\":\"c1\",", 1))} {
		if decodeProducerColdDecision(raw, digest(raw), config, d.SourceRevision) == nil {
			t.Fatal("noncanonical/unknown/duplicate accepted")
		}
	}
	if decodeProducerColdDecision(body, strings.Repeat("f", 64), config, d.SourceRevision) == nil {
		t.Fatal("wrong approval accepted")
	}
}

func TestColdProducerInitializationPersistsWithoutActivatingAnyTask(t *testing.T) {
	q := &mutationAuthorityQueue{bootstrap: true}
	p, _ := producerWithMutationAuthority(t, q, false)
	prefix := filepath.Join(filepath.Dir(p.sentinel.path), "history")
	body := []byte("exact immutable host decision")
	saves := 0
	save := func(context.Context) error { saves++; return nil }
	if err := initializeColdProducer(context.Background(), p, body, prefix, save); err != nil {
		t.Fatal(err)
	}
	if q.initializeCalls != 1 || q.activateCalls != 0 || saves != 1 {
		t.Fatal("initializer transferred task or repeated mutation")
	}
	for _, suffix := range []string{".request", ".complete"} {
		raw, err := readProducerColdFile(prefix+suffix, uint32(os.Geteuid()), false)
		if err != nil || string(raw) != string(body) {
			t.Fatal("durable history lost", err)
		}
	}
	if err := initializeColdProducer(context.Background(), p, body, prefix, save); err != nil {
		t.Fatal(err)
	}
	if q.initializeCalls != 1 || q.activateCalls != 0 || saves != 1 {
		t.Fatal("completed retry mutated authority")
	}
	if initializeColdProducer(context.Background(), p, []byte("changed"), prefix, save) == nil {
		t.Fatal("retained decision replaced")
	}
	q.setBootstrap(true)
	if initializeColdProducer(context.Background(), p, body, prefix, save) == nil {
		t.Fatal("completed Redis loss silently repaired")
	}
}

func TestColdProducerFailedSaveRecoversExactPairAndRefusesMissingHistory(t *testing.T) {
	q := &mutationAuthorityQueue{bootstrap: true}
	p, _ := producerWithMutationAuthority(t, q, false)
	prefix := filepath.Join(filepath.Dir(p.sentinel.path), "history")
	body := []byte("exact decision")
	failure := errors.New("SAVE unavailable")
	if err := initializeColdProducer(context.Background(), p, body, prefix, func(context.Context) error { return failure }); !errors.Is(err, failure) {
		t.Fatal(err)
	}
	if _, err := os.Lstat(prefix + ".complete"); !os.IsNotExist(err) {
		t.Fatal("failed SAVE published completion")
	}
	if q.initializeCalls != 1 || q.activateCalls != 0 {
		t.Fatal("failed SAVE changed tasks")
	}
	if err := initializeColdProducer(context.Background(), p, body, prefix, func(context.Context) error { return nil }); err != nil {
		t.Fatal(err)
	}
	if q.initializeCalls != 1 {
		t.Fatal("pending retry reinitialized owned pair")
	}
	if err := os.Remove(prefix + ".request"); err != nil {
		t.Fatal(err)
	}
	if initializeColdProducer(context.Background(), p, body, prefix, func(context.Context) error { t.Fatal("missing history saved"); return nil }) == nil {
		t.Fatal("missing history reconstructed")
	}
}

func TestColdProducerPreparingCrashAndOrphanAuthority(t *testing.T) {
	for _, orphan := range []bool{false, true} {
		t.Run(map[bool]string{false: "prepared-crash", true: "orphan-Redis"}[orphan], func(t *testing.T) {
			q := &mutationAuthorityQueue{bootstrap: !orphan}
			p, sentinel := producerWithMutationAuthority(t, q, false)
			prefix := filepath.Join(filepath.Dir(sentinel.path), "history")
			body := []byte("exact decision")
			if err := retainProducerColdFile(prefix+".request", body); err != nil {
				t.Fatal(err)
			}
			if !orphan {
				if err := sentinel.ensurePreparing(); err != nil {
					t.Fatal(err)
				}
			}
			err := initializeColdProducer(context.Background(), p, body, prefix, func(context.Context) error { return nil })
			if orphan {
				if err == nil || q.initializeCalls != 0 {
					t.Fatal("orphan Redis adopted")
				}
			} else if err != nil || q.initializeCalls != 1 || q.activateCalls != 0 {
				t.Fatal("preparing crash did not recover", err)
			}
		})
	}
}

func TestProducerLifecycleExcludesConcurrentServingAndInitialization(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	release, err := acquireProducerLifecycleLock(directory)
	if err != nil {
		t.Fatal(err)
	}
	if second, err := acquireProducerLifecycleLock(directory); err == nil {
		second()
		t.Fatal("concurrent producer lifecycle accepted")
	}
	release()
	second, err := acquireProducerLifecycleLock(directory)
	if err != nil {
		t.Fatal(err)
	}
	second()
}

func TestColdProducerHistoryRefusesSymlinkAndUnsafeMode(t *testing.T) {
	path := filepath.Join(t.TempDir(), "history")
	if err := os.WriteFile(path, []byte("exact"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := readProducerColdFile(path, uint32(os.Geteuid()), false); err == nil {
		t.Fatal("public history accepted")
	}
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	link := path + ".link"
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readProducerColdFile(link, uint32(os.Geteuid()), false); err == nil {
		t.Fatal("symlink history accepted")
	}
	if err := os.Link(path, path+".hardlink"); err != nil {
		t.Fatal(err)
	}
	if _, err := readProducerColdFile(path, uint32(os.Geteuid()), false); err == nil {
		t.Fatal("hard-linked history accepted")
	}
}
