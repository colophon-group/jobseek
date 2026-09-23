package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

type fakeProducerQueue struct {
	stored             *storedTask
	inspectErr         error
	initialized        int
	activated          int
	activatedTask      *queueTask
	activatedReadyAt   int64
	activatedFirstTime bool
	result             transition
	activateErr        error
	occupancy          int64
	preflightFn        func(context.Context, bool) error
}

type serializedProducerQueue struct {
	mu            sync.Mutex
	stored        *storedTask
	initialized   bool
	active        int
	maximumActive int
	revisions     []int64
}

type mutationAuthorityQueue struct {
	mu              sync.Mutex
	bootstrap       bool
	initializeCalls int
	activateCalls   int
	fullPreflights  int
	flushOnActivate bool
	sentinel        *producerActivationSentinel
	phaseAtActivate producerSentinelPhase
}

func (q *mutationAuthorityQueue) preflight(_ context.Context, full bool, _ producerOwnerIdentity) (bool, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	if full {
		q.fullPreflights++
	}
	return q.bootstrap, nil
}

func (q *mutationAuthorityQueue) initializeProducer(context.Context, producerOwnerIdentity) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.initializeCalls++
	q.bootstrap = false
	return nil
}

func (q *mutationAuthorityQueue) lifetimeOccupancy(context.Context) (int64, error) { return 0, nil }

func (q *mutationAuthorityQueue) inspect(context.Context, string) (*storedTask, error) {
	return nil, nil
}

func (q *mutationAuthorityQueue) activateLegacy(context.Context, *queueTask, int64, string, string, bool, bool, producerOwnerIdentity) (transition, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.activateCalls++
	if q.sentinel != nil {
		q.phaseAtActivate, _ = q.sentinel.state()
	}
	if q.flushOnActivate {
		q.bootstrap = true
		return transition{}, queueAuthority("corruption", "activate_legacy")
	}
	return transition{Decision: "accepted", Reason: "activated"}, nil
}

func (q *mutationAuthorityQueue) setBootstrap(value bool) {
	q.mu.Lock()
	q.bootstrap = value
	q.mu.Unlock()
}

func (q *mutationAuthorityQueue) counts() (int, int, int, producerSentinelPhase) {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.initializeCalls, q.activateCalls, q.fullPreflights, q.phaseAtActivate
}

func (q *serializedProducerQueue) preflight(context.Context, bool, producerOwnerIdentity) (bool, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	return !q.initialized, nil
}

func (q *serializedProducerQueue) initializeProducer(context.Context, producerOwnerIdentity) error {
	q.mu.Lock()
	q.initialized = true
	q.mu.Unlock()
	return nil
}

func (q *serializedProducerQueue) lifetimeOccupancy(context.Context) (int64, error) { return 0, nil }

func (q *serializedProducerQueue) inspect(_ context.Context, _ string) (*storedTask, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.stored == nil {
		return nil, nil
	}
	copy := *q.stored
	return &copy, nil
}

func (q *serializedProducerQueue) activateLegacy(_ context.Context, task *queueTask, _ int64, _, _ string, _, _ bool, _ producerOwnerIdentity) (transition, error) {
	q.mu.Lock()
	q.active++
	if q.active > q.maximumActive {
		q.maximumActive = q.active
	}
	q.mu.Unlock()
	time.Sleep(20 * time.Millisecond)
	q.mu.Lock()
	defer q.mu.Unlock()
	reason := "activated"
	if q.stored != nil {
		if q.stored.State == "terminal" || q.stored.State == "dead" {
			reason = "reactivated"
		} else {
			reason = "already_activated"
		}
	}
	copy := *task
	q.stored = &storedTask{Task: copy, State: "ready"}
	q.revisions = append(q.revisions, task.Envelope.ConfigRevision)
	q.active--
	return transition{Decision: "accepted", Reason: reason}, nil
}

func (q *fakeProducerQueue) preflight(ctx context.Context, full bool, _ producerOwnerIdentity) (bool, error) {
	if q.preflightFn != nil {
		return false, q.preflightFn(ctx, full)
	}
	return q.initialized == 0, nil
}

func (q *fakeProducerQueue) initializeProducer(context.Context, producerOwnerIdentity) error {
	q.initialized++
	return nil
}

func (q *fakeProducerQueue) lifetimeOccupancy(context.Context) (int64, error) {
	return q.occupancy, nil
}

func (q *fakeProducerQueue) inspect(context.Context, string) (*storedTask, error) {
	return q.stored, q.inspectErr
}

func (q *fakeProducerQueue) activateLegacy(_ context.Context, task *queueTask, readyAt int64, _, _ string, _ bool, firstTime bool, _ producerOwnerIdentity) (transition, error) {
	q.activated++
	q.activatedTask = task
	q.activatedReadyAt = readyAt
	q.activatedFirstTime = firstTime
	if q.result.Decision == "" {
		return transition{Decision: "accepted", Reason: "activated"}, q.activateErr
	}
	return q.result, q.activateErr
}

func validProducerRequest() producerRequest {
	return producerRequest{
		Version: producerProtocol, Operation: "prepare", Domain: "jobs.example.com",
		PostingID: "00000000-0000-4000-8000-000000000001", NextScrapeAtMS: 123_000,
		Config: map[string]string{
			"board_id": "11111111-1111-4111-8111-111111111111", "source_url": "https://jobs.example.com/posting",
			"scrape_step": "0", "scrape_interval_hours": "24", "description_r2_hash": "",
		},
		Browser: true, OperatorTransfer: true,
	}
}

func validProducer(t *testing.T, queue *fakeProducerQueue, slug string) *b0Producer {
	t.Helper()
	parser := map[string]any{
		"browser_backend": "lightpanda", "render": true, "routing_revision": "go-b0-1",
		"timeout": 5000, "wait": "load", "wait_fallback": nil,
		"defaults": map[string]any{"title": "R&D <München>"},
	}
	metadata, err := json.Marshal(map[string]any{"scraper_type": "json-ld", "scraper_config": parser})
	if err != nil {
		t.Fatal(err)
	}
	owner := producerOwnerIdentity{
		Namespace: "production-b0", Cohort: "c1",
		Route:      routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: engineOwner},
		BoardSlugs: []string{"browser-use-careers"},
	}
	return &b0Producer{
		route:      routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: engineOwner},
		cohortName: "c1", cohort: producerCohorts["c1"], owner: owner, queue: queue,
		readBoard: func(context.Context, string) (map[string]string, error) {
			return map[string]string{"board_slug": slug, "metadata": string(metadata)}, nil
		},
	}
}

func TestProducerOwnsCanonicalTaskConstruction(t *testing.T) {
	queue := &fakeProducerQueue{}
	prepared, err := validProducer(t, queue, "browser-use-careers").prepare(context.Background(), validProducerRequest())
	if err != nil {
		t.Fatal(err)
	}
	const expectedDigest = "257327a045c8fad3db0519020982bbcb4aa608f7aa32c823a633143155d44fdf"
	if prepared.legacy || prepared.task.PayloadSHA256 != expectedDigest || !hex256.MatchString(prepared.digest) {
		t.Fatalf("Go preparation lost canonical identity: %#v", prepared)
	}
	if !strings.Contains(prepared.task.Payload, `"title":"R&D <München>"`) || strings.Contains(prepared.task.Payload, `\u003c`) {
		t.Fatalf("task payload did not match Python canonical UTF-8 JSON: %s", prepared.task.Payload)
	}
	if queue.initialized != 0 || queue.activated != 0 {
		t.Fatal("read-only preparation mutated queue lifecycle")
	}
}

func TestProducerReturnsOnlyLiteralLegacyForOutsideCohort(t *testing.T) {
	queue := &fakeProducerQueue{inspectErr: errors.New("must not inspect")}
	request := validProducerRequest()
	request.FirstTime = true
	prepared, err := validProducer(t, queue, "other-careers").prepare(context.Background(), request)
	if err != nil || !prepared.legacy || prepared.digest != "" {
		t.Fatalf("outside cohort was not literal legacy: %#v, %v", prepared, err)
	}
}

func TestProducerOwnsExactSortedCohortManifest(t *testing.T) {
	producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
	producer.cohortName = "c4"
	producer.cohort = producerCohorts["c4"]
	request := producerRequest{
		Version: producerProtocol, Operation: "manifest", Cohort: "c4",
		Config: map[string]string{}, OperatorTransfer: true,
	}
	slugs, err := producer.manifest(request)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"browser-use-careers", "eclypsium-careers", "kandou-ai-careers", "poke-and-wiggle-careers"}
	if !slices.Equal(slugs, want) {
		t.Fatalf("Go cohort manifest drifted: got %v, want %v", slugs, want)
	}
	request.OperatorTransfer = false
	if _, err := producer.manifest(request); err == nil {
		t.Fatal("ordinary runtime caller obtained the operator manifest")
	}
}

func TestProducerOrdinaryRuntimeUsesOneAtomicEnqueue(t *testing.T) {
	queue := &fakeProducerQueue{}
	producer := validProducer(t, queue, "browser-use-careers")
	request := validProducerRequest()
	request.Operation, request.OperatorTransfer = "enqueue", false
	prepared, result, err := producer.enqueue(context.Background(), request)
	if err != nil || prepared.legacy || result.Reason != "activated" {
		t.Fatalf("atomic enqueue failed: %#v %#v %v", prepared, result, err)
	}
	if queue.initialized != 1 || queue.activated != 1 {
		t.Fatalf("atomic enqueue performed the wrong mutation count: %d/%d", queue.initialized, queue.activated)
	}
	if _, err := producer.prepare(context.Background(), request); err == nil {
		t.Fatal("ordinary runtime caller reached two-phase preparation")
	}
	request.OperatorTransfer = true
	if _, _, err := producer.enqueue(context.Background(), request); err == nil {
		t.Fatal("operator transfer caller used ordinary atomic enqueue")
	}
}

func producerWithMutationAuthority(
	t *testing.T, queue *mutationAuthorityQueue, sentinelActive bool,
) (*b0Producer, *producerActivationSentinel) {
	t.Helper()
	producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
	producer.queue = queue
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	sentinel, err := newProducerActivationSentinel(
		filepath.Join(directory, ".activation-v1"), producer.owner, uint32(os.Geteuid()),
	)
	if err != nil {
		t.Fatal(err)
	}
	if sentinelActive {
		if err := sentinel.ensurePreparing(); err != nil {
			t.Fatal(err)
		}
		if err := sentinel.publishActive(); err != nil {
			t.Fatal(err)
		}
	}
	producer.sentinel = sentinel
	queue.sentinel = sentinel
	return producer, sentinel
}

func TestProducerInitializesOnlyTheExactFreshAuthorityPair(t *testing.T) {
	queue := &mutationAuthorityQueue{bootstrap: true}
	producer, sentinel := producerWithMutationAuthority(t, queue, false)
	request := validProducerRequest()
	request.Operation, request.OperatorTransfer = "enqueue", false
	if _, result, err := producer.enqueue(context.Background(), request); err != nil || result.Reason != "activated" {
		t.Fatalf("fresh producer pair did not activate: %#v %v", result, err)
	}
	initialized, activated, full, phase := queue.counts()
	if initialized != 1 || activated != 1 || full != 1 {
		t.Fatalf("fresh pair did not receive its post-bootstrap full audit: %d/%d/%d", initialized, activated, full)
	}
	if phase != producerSentinelActive {
		t.Fatalf("task activation preceded active sentinel phase: %s", phase)
	}
	if active, err := sentinel.isActive(); err != nil || !active {
		t.Fatalf("fresh pair did not persist its marker: %v %v", active, err)
	}
}

func TestInitializedProducerSecondSentinelReadFailsClosed(t *testing.T) {
	for _, test := range []struct {
		name  string
		state producerSentinelPhase
		err   error
	}{
		{name: "disappeared", state: producerSentinelAbsent},
		{name: "read-error", state: producerSentinelActive, err: errors.New("read failed")},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, err := initializedSentinelNeedsPersistence(test.state, test.err); err == nil {
				t.Fatal("unsafe second sentinel observation was accepted")
			}
		})
	}
	if persist, err := initializedSentinelNeedsPersistence(producerSentinelPreparing, nil); err != nil || !persist {
		t.Fatalf("preparing sentinel did not require persistence: persist=%t err=%v", persist, err)
	}
	if persist, err := initializedSentinelNeedsPersistence(producerSentinelActive, nil); err != nil || persist {
		t.Fatalf("active sentinel was not accepted exactly: persist=%t err=%v", persist, err)
	}
}

func TestProducerDoesNotHealFlushBetweenPeriodicCheckAndEnqueue(t *testing.T) {
	queue := &mutationAuthorityQueue{}
	producer, _ := producerWithMutationAuthority(t, queue, true)
	if err := producer.preflight(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	queue.setBootstrap(true) // Redis lost after the periodic full audit.
	request := validProducerRequest()
	request.Operation, request.OperatorTransfer = "enqueue", false
	if _, _, err := producer.enqueue(context.Background(), request); err == nil {
		t.Fatal("enqueue recreated Redis after live authority loss")
	}
	initialized, activated, full, _ := queue.counts()
	if initialized != 0 || activated != 0 || full != 1 {
		t.Fatalf("lost Redis authority reached initialization/activation: %d/%d/%d", initialized, activated, full)
	}
}

func TestProducerDoesNotHealDeletedSentinelWhileRedisIsActive(t *testing.T) {
	queue := &mutationAuthorityQueue{}
	producer, sentinel := producerWithMutationAuthority(t, queue, true)
	if err := producer.preflight(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	if err := sentinel.clear(); err != nil {
		t.Fatal(err)
	}
	request := validProducerRequest()
	request.Operation, request.OperatorTransfer = "enqueue", false
	if _, _, err := producer.enqueue(context.Background(), request); err == nil {
		t.Fatal("enqueue recreated a deleted active sentinel")
	}
	initialized, activated, full, _ := queue.counts()
	if initialized != 0 || activated != 0 || full != 1 {
		t.Fatalf("missing active sentinel reached initialization/activation: %d/%d/%d", initialized, activated, full)
	}
}

func TestProducerDoesNotReinitializeAfterAuthorityLossInsideActivation(t *testing.T) {
	queue := &mutationAuthorityQueue{flushOnActivate: true}
	producer, _ := producerWithMutationAuthority(t, queue, true)
	request := validProducerRequest()
	request.Operation, request.OperatorTransfer = "enqueue", false
	if _, _, err := producer.enqueue(context.Background(), request); err == nil {
		t.Fatal("activation-time Redis loss was accepted")
	}
	queue.flushOnActivate = false
	if _, _, err := producer.enqueue(context.Background(), request); err == nil {
		t.Fatal("retry healed activation-time Redis loss")
	}
	initialized, activated, full, _ := queue.counts()
	if initialized != 0 || activated != 1 || full != 0 {
		t.Fatalf("activation-time loss was reinitialized: %d/%d/%d", initialized, activated, full)
	}
}

func TestProcessCancellationIsNotProducerAuthorityLoss(t *testing.T) {
	if class, lost := authorityErrorClass(context.Canceled); lost || class != "" {
		t.Fatalf("process cancellation was classified as authority loss: %q", class)
	}
	if err := transitionError("initialize", transition{}, context.Canceled); !errors.Is(err, context.Canceled) {
		t.Fatalf("queue transition hid process cancellation: %v", err)
	}
	queue := &fakeProducerQueue{inspectErr: context.Canceled}
	_, err := validProducer(t, queue, "browser-use-careers").prepare(context.Background(), validProducerRequest())
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("producer hid process cancellation: %v", err)
	}
}

func TestProducerConfigNeverGrantsMutationToItsOwnUID(t *testing.T) {
	for name, value := range map[string]string{
		"LIGHTPANDA_B0_PRODUCER_MODE":       "enabled",
		"LIGHTPANDA_B0_PRODUCER_COHORT":     "c1",
		"LIGHTPANDA_B0_PRODUCER_SOCKET":     producerSocketPath,
		"LIGHTPANDA_B0_QUEUE_NAMESPACE":     "production-b0",
		"LIGHTPANDA_B0_SHARD_ID":            "lightpanda-b0",
		"LIGHTPANDA_B0_ROUTING_EPOCH":       "7",
		"REDIS_URL":                         "redis://localhost:6379/0",
		"LIGHTPANDA_B0_PRODUCER_CLIENT_UID": strconv.Itoa(os.Geteuid()),
	} {
		t.Setenv(name, value)
	}
	if _, err := producerConfigFromEnvironment(); err == nil {
		t.Fatal("producer granted mutation authority to its own UID")
	}
	t.Setenv("LIGHTPANDA_B0_PRODUCER_CLIENT_UID", strconv.Itoa(os.Geteuid()+1))
	configured, err := producerConfigFromEnvironment()
	if err != nil || configured.ClientUID != uint32(os.Geteuid()+1) {
		t.Fatalf("distinct mutation UID was rejected: %#v %v", configured, err)
	}
}

func TestProducerRejectsIdentityDriftBeforeMutation(t *testing.T) {
	queue := &fakeProducerQueue{}
	producer := validProducer(t, queue, "browser-use-careers")
	request := validProducerRequest()
	request.Config["domain"] = "other.example.com"
	if _, err := producer.prepare(context.Background(), request); err == nil {
		t.Fatal("legacy config domain drift was accepted")
	}
	producer.readBoard = func(context.Context, string) (map[string]string, error) {
		return map[string]string{
			"board_slug": "browser-use-careers",
			"metadata":   `{"scraper_type":7,"scraper_config":{}}`,
		}, nil
	}
	request.Config["domain"] = request.Domain
	if _, err := producer.prepare(context.Background(), request); err == nil {
		t.Fatal("non-string scraper type was treated as an absent default")
	}
	if queue.initialized != 0 || queue.activated != 0 {
		t.Fatal("identity rejection mutated queue lifecycle")
	}
}

func TestProducerActivationIsDigestCheckedAndIdempotent(t *testing.T) {
	queue := &fakeProducerQueue{}
	producer := validProducer(t, queue, "browser-use-careers")
	request := validProducerRequest()
	first, err := producer.prepare(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	request.Operation, request.ExpectedDigest = "activate", strings.Repeat("0", 64)
	if _, _, err := producer.activate(context.Background(), request); err == nil {
		t.Fatal("changed preparation digest was accepted")
	}
	if queue.initialized != 0 || queue.activated != 0 {
		t.Fatal("digest rejection mutated queue lifecycle")
	}
	queue.stored = &storedTask{Task: first.task, State: "ready"}
	queue.result = transition{Decision: "accepted", Reason: "already_activated"}
	request.ExpectedDigest = first.digest
	prepared, result, err := producer.activate(context.Background(), request)
	if err != nil || prepared.digest != first.digest || result.Reason != "already_activated" {
		t.Fatalf("idempotent activation retry failed: %#v %#v %v", prepared, result, err)
	}
	if queue.initialized != 1 || queue.activated != 1 || queue.activatedTask.PayloadSHA256 != first.task.PayloadSHA256 {
		t.Fatal("idempotent activation used the wrong queue transition")
	}
}

func TestProducerAdvancesOnlyTerminalRevision(t *testing.T) {
	queue := &fakeProducerQueue{}
	producer := validProducer(t, queue, "browser-use-careers")
	request := validProducerRequest()
	base, err := producer.prepare(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	terminal := base.task
	terminal.Envelope.ConfigRevision = 3
	terminal.Payload = strings.Replace(terminal.Payload, `"config_revision":1`, `"config_revision":3`, 1)
	digest := sha256Bytes([]byte(terminal.Payload))
	terminal, err = decodeQueueTask(terminal.Payload, digest, producer.route)
	if err != nil {
		t.Fatal(err)
	}
	queue.stored = &storedTask{Task: terminal, State: "terminal"}
	prepared, err := producer.prepare(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if prepared.task.Envelope.ConfigRevision != 4 || prepared.previousPayloadSHA256 != terminal.PayloadSHA256 {
		t.Fatalf("terminal revision did not advance exactly: %#v", prepared)
	}
}

func TestProducerCarriesEarlierAndFirstTimeDueIntentForExistingTask(t *testing.T) {
	for _, test := range []struct {
		name      string
		state     string
		firstTime bool
		readyAt   int64
	}{
		{name: "ready-earlier", state: "ready", readyAt: 12_000},
		{name: "inflight-first-time", state: "inflight", firstTime: true, readyAt: 0},
	} {
		t.Run(test.name, func(t *testing.T) {
			queue := &fakeProducerQueue{}
			producer := validProducer(t, queue, "browser-use-careers")
			base, err := producer.prepare(context.Background(), validProducerRequest())
			if err != nil {
				t.Fatal(err)
			}
			queue.stored = &storedTask{Task: base.task, State: test.state}
			request := validProducerRequest()
			request.Operation, request.OperatorTransfer = "enqueue", false
			request.NextScrapeAtMS, request.FirstTime = test.readyAt, test.firstTime
			if _, _, err := producer.enqueue(context.Background(), request); err != nil {
				t.Fatal(err)
			}
			if queue.activatedTask == nil || queue.activatedTask.Envelope.InitialReadyAtMS != 123_000 ||
				queue.activatedReadyAt != test.readyAt || queue.activatedFirstTime != test.firstTime {
				t.Fatalf("requested due intent was replaced by frozen envelope: %#v", queue)
			}
		})
	}
}

func TestProducerRejectsNonzeroExternalFirstTimeSchedule(t *testing.T) {
	request := validProducerRequest()
	request.FirstTime = true
	if _, err := validProducer(t, &fakeProducerQueue{}, "browser-use-careers").prepare(context.Background(), request); err == nil {
		t.Fatal("nonzero external first-time schedule was silently rewritten or accepted")
	}
}

func TestProducerSerializesConcurrentNewAndTerminalTaskMutation(t *testing.T) {
	for _, terminal := range []bool{false, true} {
		name := "new"
		if terminal {
			name = "terminal"
		}
		t.Run(name, func(t *testing.T) {
			queue := &serializedProducerQueue{}
			producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
			producer.queue = queue
			if terminal {
				base, err := producer.prepare(context.Background(), validProducerRequest())
				if err != nil {
					t.Fatal(err)
				}
				task := base.task
				task.Envelope.ConfigRevision = 3
				task.Payload = strings.Replace(task.Payload, `"config_revision":1`, `"config_revision":3`, 1)
				decoded, err := decodeQueueTask(task.Payload, sha256Bytes([]byte(task.Payload)), producer.route)
				if err != nil {
					t.Fatal(err)
				}
				queue.stored = &storedTask{Task: decoded, State: "terminal"}
			}
			start := make(chan struct{})
			errorsFound := make(chan error, 2)
			results := make(chan transition, 2)
			var callers sync.WaitGroup
			for _, readyAt := range []int64{123_000, 456_000} {
				request := validProducerRequest()
				request.Operation, request.OperatorTransfer, request.NextScrapeAtMS = "enqueue", false, readyAt
				callers.Add(1)
				go func() {
					defer callers.Done()
					<-start
					_, result, err := producer.enqueue(context.Background(), request)
					if err != nil {
						errorsFound <- err
						return
					}
					results <- result
				}()
			}
			close(start)
			callers.Wait()
			close(errorsFound)
			close(results)
			for err := range errorsFound {
				t.Fatalf("serialized producer request failed: %v", err)
			}
			if len(results) != 2 || queue.maximumActive != 1 || queue.stored == nil {
				t.Fatalf("same-task mutation was not serialized: results=%d max=%d", len(results), queue.maximumActive)
			}
			wantRevision := int64(1)
			if terminal {
				wantRevision = 4
			}
			if queue.stored.Task.Envelope.ConfigRevision != wantRevision || len(queue.revisions) != 2 ||
				queue.revisions[0] != wantRevision || queue.revisions[1] != wantRevision {
				t.Fatalf("same-task race advanced revision inconsistently: %#v", queue.revisions)
			}
		})
	}
}

func TestProducerControlRequiresCanonicalStrictJSON(t *testing.T) {
	request := validProducerRequest()
	payload, err := canonicalJSON(request, true)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := decodeProducerRequest(payload); err != nil {
		t.Fatalf("canonical request rejected: %v", err)
	}
	noncanonical := bytes.Replace(payload, []byte(`"browser":true`), []byte(`"browser": true`), 1)
	if _, err := decodeProducerRequest(noncanonical); err == nil {
		t.Fatal("noncanonical request accepted")
	}
	unknown := append(append([]byte{}, payload[:len(payload)-1]...), []byte(`,"unknown":true}`)...)
	if _, err := decodeProducerRequest(unknown); err == nil {
		t.Fatal("unknown producer field accepted")
	}
	var fields map[string]any
	if err := json.Unmarshal(payload, &fields); err != nil {
		t.Fatal(err)
	}
	delete(fields, "first_time")
	missing, err := canonicalJSON(fields, true)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := decodeProducerRequest(missing); err == nil {
		t.Fatal("producer request missing first_time was accepted")
	}
	var output bytes.Buffer
	if err := writeProducerResponse(&output, producerFailure("authority_lost")); err != nil {
		t.Fatal(err)
	}
	response, err := framing.ReadRecord(&output, producerFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	value, err := parseCanonicalValue(response)
	if err != nil {
		t.Fatal(err)
	}
	canonical, _ := canonicalJSON(value, true)
	if !bytes.Equal(canonical, response) || output.Len() != 0 {
		t.Fatal("producer response was not one canonical framed record")
	}
}

func sha256Bytes(value []byte) string {
	digest := sha256.Sum256(value)
	return hex.EncodeToString(digest[:])
}
