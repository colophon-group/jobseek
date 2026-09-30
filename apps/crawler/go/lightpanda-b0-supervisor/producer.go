package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"math"
	"os"
	"sort"
	"strconv"
	"sync"
	"time"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/redis/go-redis/v9"
)

const (
	producerProtocol          = "jobseek.lightpanda.producer/v1"
	producerSocketPath        = "/run/jobseek-lightpanda-producer/control.sock"
	producerSentinelPath      = "/run/jobseek-lightpanda-producer/.activation-v1"
	producerFrameLimit        = uint64(256 * 1024)
	producerTimeout           = 3 * time.Second
	producerPreflightInterval = 2 * time.Second
	producerBacklog           = 72 // 67 discovery callers plus bounded healthcheck headroom.
	producerMaxHandlers       = 8
	producerTaskStripes       = 32
)

var producerCohorts = map[string]map[string]struct{}{
	"c1":   set("browser-use-careers"),
	"c2":   set("browser-use-careers", "kandou-ai-careers"),
	"c3":   set("browser-use-careers", "eclypsium-careers", "kandou-ai-careers"),
	"cdom": set("browser-use-careers", "bunq-careers", "algorized-careers"),
	// c4 is retained for the frozen four-origin admission fixture, not production cutover.
	"c4": set("browser-use-careers", "eclypsium-careers", "kandou-ai-careers", "poke-and-wiggle-careers"),
}

type producerConfig struct {
	RedisOptions *redis.Options
	LuaPath      string
	Namespace    string
	Route        routeIdentity
	Cohort       string
	Socket       string
	DefaultDelay string
	ClientUID    uint32
}

func producerConfigFromEnvironment() (producerConfig, error) {
	if requiredEnv("LIGHTPANDA_B0_PRODUCER_MODE") != modeEnabled {
		return producerConfig{}, errors.New("LIGHTPANDA_B0_PRODUCER_MODE must be exactly enabled")
	}
	redisOptions, err := redis.ParseURL(requiredEnv("REDIS_URL"))
	if err != nil || redisOptions == nil {
		return producerConfig{}, errors.New("REDIS_URL must be a valid standalone Redis URL")
	}
	rawEpoch := requiredEnv("LIGHTPANDA_B0_ROUTING_EPOCH")
	epoch, err := strconv.ParseInt(rawEpoch, 10, 64)
	if err != nil || strconv.FormatInt(epoch, 10) != rawEpoch {
		return producerConfig{}, errors.New("LIGHTPANDA_B0_ROUTING_EPOCH must be canonical")
	}
	rawClientUID := requiredEnv("LIGHTPANDA_B0_PRODUCER_CLIENT_UID")
	clientUID, err := strconv.ParseUint(rawClientUID, 10, 32)
	if err != nil || strconv.FormatUint(clientUID, 10) != rawClientUID {
		return producerConfig{}, errors.New("LIGHTPANDA_B0_PRODUCER_CLIENT_UID must be canonical")
	}
	result := producerConfig{
		RedisOptions: redisOptions,
		LuaPath:      env("LIGHTPANDA_B0_LUA_PATH", "/app/src/lua/lightpanda_b0_queue.lua"),
		Namespace:    requiredEnv("LIGHTPANDA_B0_QUEUE_NAMESPACE"),
		Route: routeIdentity{
			ShardID: requiredEnv("LIGHTPANDA_B0_SHARD_ID"), RoutingEpoch: epoch, EngineOwner: engineOwner,
		},
		Cohort:       requiredEnv("LIGHTPANDA_B0_PRODUCER_COHORT"),
		Socket:       env("LIGHTPANDA_B0_PRODUCER_SOCKET", producerSocketPath),
		DefaultDelay: env("THROTTLE_DELAY_DEFAULT", "2.0"),
		ClientUID:    uint32(clientUID),
	}
	if _, ok := producerCohorts[result.Cohort]; !ok || !safeID.MatchString(result.Namespace) ||
		(result.Cohort == "c4" && result.Namespace != "admission-b0") ||
		result.Socket != producerSocketPath || result.ClientUID == uint32(os.Geteuid()) {
		return producerConfig{}, errors.New("invalid fixed B0 producer identity")
	}
	if err := result.Route.validate(); err != nil {
		return producerConfig{}, err
	}
	return result, nil
}

type producerAuthorityError struct {
	class string
}

func (failure producerAuthorityError) Error() string {
	return "producer authority lost: " + failure.class
}

func authorityLost(class string) error {
	if !contains(set("redis", "fenced", "corruption"), class) {
		class = "corruption"
	}
	return producerAuthorityError{class: class}
}

func authorityErrorClass(err error) (string, bool) {
	var failure producerAuthorityError
	if errors.As(err, &failure) {
		return failure.class, true
	}
	var queueFailure queueAuthorityError
	if errors.As(err, &queueFailure) {
		return queueFailure.class, true
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "redis", true
	}
	return "", false
}

type producerRequest struct {
	Version             string            `json:"version"`
	Operation           string            `json:"operation"`
	Cohort              string            `json:"cohort"`
	Domain              string            `json:"domain"`
	PostingID           string            `json:"posting_id"`
	NextScrapeAtMS      int64             `json:"next_scrape_at_ms"`
	Config              map[string]string `json:"config"`
	Browser             bool              `json:"browser"`
	FirstTime           bool              `json:"first_time"`
	OperatorTransfer    bool              `json:"operator_transfer"`
	ExpectedDigest      string            `json:"expected_digest"`
	LegacyScheduleScore string            `json:"legacy_schedule_score,omitempty"`
}

type producerResponse struct {
	Version               string   `json:"version"`
	Outcome               string   `json:"outcome"`
	Reason                string   `json:"reason"`
	PreparationDigest     string   `json:"preparation_digest"`
	PayloadSHA256         string   `json:"payload_sha256"`
	ExistingState         string   `json:"existing_state"`
	ExistingPayloadSHA256 string   `json:"existing_payload_sha256"`
	Activated             bool     `json:"activated"`
	Cohort                string   `json:"cohort"`
	BoardSlugs            []string `json:"board_slugs"`
	LifetimeOccupancy     int64    `json:"lifetime_occupancy"`
	LifetimeCapacity      int64    `json:"lifetime_capacity"`
	LifetimeHeadroom      int64    `json:"lifetime_headroom"`
}

type producerQueue interface {
	preflight(context.Context, bool, producerOwnerIdentity) (bool, error)
	initializeProducer(context.Context, producerOwnerIdentity) error
	lifetimeOccupancy(context.Context) (int64, error)
	inspect(context.Context, string) (*storedTask, error)
	activateLegacy(context.Context, *queueTask, int64, string, string, bool, bool, producerOwnerIdentity) (transition, error)
}

func (p *b0Producer) preflight(ctx context.Context, full bool) error {
	if p == nil || p.queue == nil || ctx == nil {
		return authorityLost("corruption")
	}
	release, err := p.bootstrap.acquire(ctx)
	if err != nil {
		return err
	}
	defer release()
	_, err = p.preflightLocked(ctx, full)
	return err
}

func (p *b0Producer) preflightLocked(ctx context.Context, full bool) (bool, error) {
	bootstrap, err := p.queue.preflight(ctx, full, p.owner)
	if err != nil {
		return false, producerQueueAuthority(err)
	}
	if p.sentinel != nil {
		state, sentinelErr := p.sentinel.state()
		validPair := (bootstrap && (state == producerSentinelAbsent || state == producerSentinelPreparing)) ||
			(!bootstrap && (state == producerSentinelPreparing || state == producerSentinelActive))
		if sentinelErr != nil || !validPair {
			return false, authorityLost("corruption")
		}
	}
	return bootstrap, nil
}

func (p *b0Producer) health(ctx context.Context, request producerRequest, acceptedUID uint32) error {
	if request.Version != producerProtocol || request.Operation != "health" || request.Cohort != "" ||
		request.Domain != "" || request.PostingID != "" || request.NextScrapeAtMS != 0 ||
		len(request.Config) != 1 || request.Config["client_uid"] != strconv.FormatUint(uint64(acceptedUID), 10) ||
		request.Browser || request.FirstTime || request.OperatorTransfer || request.ExpectedDigest != "" {
		return errors.New("invalid producer health request")
	}
	return p.preflight(ctx, true)
}

type producerOwnerIdentity struct {
	Namespace  string
	Cohort     string
	Route      routeIdentity
	BoardSlugs []string
}

func (owner producerOwnerIdentity) validate() error {
	if !safeID.MatchString(owner.Namespace) || !contains(set("c1", "c2", "c3", "c4", "cdom"), owner.Cohort) ||
		owner.Route.validate() != nil || owner.Route.EngineOwner != engineOwner ||
		len(owner.BoardSlugs) == 0 || len(owner.BoardSlugs) > 16 || !sort.StringsAreSorted(owner.BoardSlugs) {
		return errors.New("invalid producer owner identity")
	}
	seen := make(map[string]struct{}, len(owner.BoardSlugs))
	for _, slug := range owner.BoardSlugs {
		if !safeID.MatchString(slug) {
			return errors.New("invalid producer owner manifest")
		}
		seen[slug] = struct{}{}
	}
	if len(seen) != len(owner.BoardSlugs) {
		return errors.New("duplicate producer owner manifest")
	}
	return nil
}

type producerStripes struct {
	once    sync.Once
	stripes [producerTaskStripes]chan struct{}
}

type producerGate struct {
	once  sync.Once
	token chan struct{}
}

func (gate *producerGate) acquire(ctx context.Context) (func(), error) {
	if ctx == nil {
		return nil, errors.New("invalid producer bootstrap lock")
	}
	gate.once.Do(func() {
		gate.token = make(chan struct{}, 1)
		gate.token <- struct{}{}
	})
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-gate.token:
		return func() { gate.token <- struct{}{} }, nil
	}
}

func (stripes *producerStripes) acquire(ctx context.Context, taskID string) (func(), error) {
	if ctx == nil || !safeID.MatchString(taskID) {
		return nil, errors.New("invalid producer task lock")
	}
	stripes.once.Do(func() {
		for index := range stripes.stripes {
			stripes.stripes[index] = make(chan struct{}, 1)
			stripes.stripes[index] <- struct{}{}
		}
	})
	digest := sha256.Sum256([]byte(taskID))
	stripe := stripes.stripes[int(digest[0])%len(stripes.stripes)]
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-stripe:
		return func() { stripe <- struct{}{} }, nil
	}
}

type b0Producer struct {
	route      routeIdentity
	cohortName string
	cohort     map[string]struct{}
	owner      producerOwnerIdentity
	queue      producerQueue
	readBoard  func(context.Context, string) (map[string]string, error)
	sentinel   *producerActivationSentinel
	stripes    producerStripes
	bootstrap  producerGate
}

type preparedTask struct {
	legacy                bool
	task                  queueTask
	digest                string
	legacyConfig          string
	previousPayloadSHA256 string
	existingState         string
	existingPayload       string
	requestedReadyAtMS    int64
}

func newB0Producer(client *redis.Client, queue *b0Queue, cohort string, route routeIdentity) (*b0Producer, error) {
	allowed, ok := producerCohorts[cohort]
	if client == nil || queue == nil || !ok || route.validate() != nil {
		return nil, errors.New("invalid B0 producer dependencies")
	}
	slugs := make([]string, 0, len(allowed))
	for slug := range allowed {
		slugs = append(slugs, slug)
	}
	sort.Strings(slugs)
	owner := producerOwnerIdentity{Namespace: queue.namespace, Cohort: cohort, Route: route, BoardSlugs: slugs}
	if err := owner.validate(); err != nil {
		return nil, err
	}
	sentinel, err := newProducerActivationSentinel(producerSentinelPath, owner, uint32(os.Geteuid()))
	if err != nil {
		return nil, err
	}
	return &b0Producer{
		route: route, cohortName: cohort, cohort: allowed, owner: owner, queue: queue, sentinel: sentinel,
		readBoard: func(ctx context.Context, boardID string) (map[string]string, error) {
			return client.HGetAll(ctx, "board:"+boardID).Result()
		},
	}, nil
}

func (p *b0Producer) manifest(request producerRequest) ([]string, error) {
	if p == nil || request.Version != producerProtocol || request.Operation != "manifest" ||
		!request.OperatorTransfer || request.Cohort != p.cohortName || request.Domain != "" ||
		request.PostingID != "" || request.NextScrapeAtMS != 0 || request.LegacyScheduleScore != "" || len(request.Config) != 0 ||
		request.Browser || request.FirstTime || request.ExpectedDigest != "" {
		return nil, errors.New("invalid producer manifest request")
	}
	slugs := make([]string, 0, len(p.cohort))
	for slug := range p.cohort {
		slugs = append(slugs, slug)
	}
	sort.Strings(slugs)
	return slugs, nil
}

func (p *b0Producer) prepare(ctx context.Context, request producerRequest) (preparedTask, error) {
	if !request.OperatorTransfer {
		return preparedTask{}, errors.New("producer preparation is operator-only")
	}
	return p.prepareTask(ctx, request)
}

func (p *b0Producer) prepareTask(ctx context.Context, request producerRequest) (preparedTask, error) {
	if p == nil || p.queue == nil || p.readBoard == nil || request.Version != producerProtocol || request.Operation != "prepare" ||
		request.Cohort != "" || request.ExpectedDigest != "" || !safeID.MatchString(request.PostingID) || !safeDomain.MatchString(request.Domain) ||
		request.NextScrapeAtMS < 0 || request.NextScrapeAtMS > maxInteger || request.Config == nil {
		return preparedTask{}, errors.New("invalid producer preparation")
	}
	boardID := request.Config["board_id"]
	if !safeID.MatchString(boardID) {
		return preparedTask{}, errors.New("producer task has no board identity")
	}
	board, err := p.readBoard(ctx, boardID)
	if err != nil {
		if errors.Is(ctx.Err(), context.Canceled) {
			return preparedTask{}, ctx.Err()
		}
		if errors.Is(err, context.Canceled) {
			return preparedTask{}, context.Canceled
		}
		return preparedTask{}, authorityLost("redis")
	}
	slug := board["board_slug"]
	if slug == "" {
		return preparedTask{}, authorityLost("corruption")
	}
	if _, ok := p.cohort[slug]; !ok {
		return preparedTask{legacy: true}, nil
	}
	if request.LegacyScheduleScore != "" {
		score, parseErr := strconv.ParseFloat(request.LegacyScheduleScore, 64)
		if !request.OperatorTransfer || len(request.LegacyScheduleScore) > 32 || parseErr != nil ||
			math.IsNaN(score) || math.IsInf(score, 0) || score < 0 || score > float64(maxInteger)/1000 {
			return preparedTask{}, errors.New("invalid legacy schedule score binding")
		}
	}
	if request.FirstTime && request.NextScrapeAtMS != 0 && (!request.OperatorTransfer || request.LegacyScheduleScore == "") {
		return preparedTask{}, errors.New("B0 first-time work must be immediately ready")
	}
	if !request.Browser || request.Config["scrape_step"] != "0" {
		return preparedTask{}, errors.New("allowlisted B0 work is not browser step zero")
	}
	parserConfig, assignment, err := producerAssignment(board["metadata"])
	if err != nil {
		return preparedTask{}, authorityLost("corruption")
	}
	wanted, err := buildProducerTask(request, p.route, parserConfig, assignment, 1)
	if err != nil {
		return preparedTask{}, err
	}
	if _, err := p.queue.preflight(ctx, false, p.owner); err != nil {
		return preparedTask{}, producerQueueAuthority(err)
	}
	existing, err := p.queue.inspect(ctx, request.PostingID)
	if err != nil {
		if errors.Is(ctx.Err(), context.Canceled) {
			return preparedTask{}, ctx.Err()
		}
		if errors.Is(err, context.Canceled) {
			return preparedTask{}, context.Canceled
		}
		if class, ok := authorityErrorClass(err); ok {
			return preparedTask{}, authorityLost(class)
		}
		return preparedTask{}, authorityLost("corruption")
	}
	desired := wanted
	previous, state, existingPayload := "", "", ""
	if existing != nil {
		state, existingPayload, previous = existing.State, existing.Task.PayloadSHA256, existing.Task.PayloadSHA256
		if existing.State == "ready" || existing.State == "inflight" {
			if !sameProducerIdentity(existing.Task, wanted) {
				return preparedTask{}, authorityLost("corruption")
			}
			desired = existing.Task
		} else {
			if existing.Task.Envelope.ConfigRevision >= maxInteger {
				return preparedTask{}, errors.New("B0 config revision is exhausted")
			}
			desired, err = buildProducerTask(request, p.route, parserConfig, assignment, existing.Task.Envelope.ConfigRevision+1)
			if err != nil {
				return preparedTask{}, err
			}
		}
	}
	legacyConfig, err := producerLegacyConfig(request.Config, desired, request.LegacyScheduleScore)
	if err != nil {
		return preparedTask{}, err
	}
	digestDocument := map[string]any{
		"browser": request.Browser, "config": request.Config, "domain": request.Domain,
		"first_time":        request.FirstTime,
		"next_scrape_at_ms": request.NextScrapeAtMS, "operator_transfer": request.OperatorTransfer,
		"posting_id": request.PostingID, "task_payload_sha256": desired.PayloadSHA256,
		"version": producerProtocol,
	}
	if request.LegacyScheduleScore != "" {
		digestDocument["legacy_schedule_score"] = request.LegacyScheduleScore
	}
	canonical, err := canonicalJSON(digestDocument, true)
	if err != nil {
		return preparedTask{}, err
	}
	digest := sha256.Sum256(canonical)
	return preparedTask{
		task: desired, digest: hex.EncodeToString(digest[:]), legacyConfig: legacyConfig,
		previousPayloadSHA256: previous, existingState: state, existingPayload: existingPayload,
		requestedReadyAtMS: request.NextScrapeAtMS,
	}, nil
}

func (p *b0Producer) enqueue(ctx context.Context, request producerRequest) (preparedTask, transition, error) {
	if request.Operation != "enqueue" || request.OperatorTransfer || request.ExpectedDigest != "" {
		return preparedTask{}, transition{}, errors.New("invalid atomic producer request")
	}
	release, err := p.stripes.acquire(ctx, request.PostingID)
	if err != nil {
		return preparedTask{}, transition{}, err
	}
	defer release()
	prepareRequest := request
	prepareRequest.Operation = "prepare"
	prepared, err := p.prepareTask(ctx, prepareRequest)
	if err != nil || prepared.legacy {
		return prepared, transition{}, err
	}
	releaseAuthority, err := p.acquireMutationAuthority(ctx)
	if err != nil {
		return preparedTask{}, transition{}, producerQueueAuthority(err)
	}
	defer releaseAuthority()
	return p.activatePrepared(ctx, request, prepared, false)
}

func (p *b0Producer) activate(ctx context.Context, request producerRequest) (preparedTask, transition, error) {
	if !hex256.MatchString(request.ExpectedDigest) || request.Operation != "activate" || !request.OperatorTransfer || request.Cohort != "" {
		return preparedTask{}, transition{}, errors.New("invalid producer activation digest")
	}
	release, err := p.stripes.acquire(ctx, request.PostingID)
	if err != nil {
		return preparedTask{}, transition{}, err
	}
	defer release()
	prepareRequest := request
	prepareRequest.Operation, prepareRequest.ExpectedDigest = "prepare", ""
	prepared, err := p.prepareTask(ctx, prepareRequest)
	if err != nil || prepared.legacy || prepared.digest != request.ExpectedDigest {
		if err == nil {
			err = errProducerDigestMismatch
		}
		return preparedTask{}, transition{}, err
	}
	releaseAuthority, err := p.acquireMutationAuthority(ctx)
	if err != nil {
		return preparedTask{}, transition{}, producerQueueAuthority(err)
	}
	defer releaseAuthority()
	return p.activatePrepared(ctx, request, prepared, true)
}

func (p *b0Producer) activatePrepared(ctx context.Context, request producerRequest, prepared preparedTask, operatorTransfer bool) (preparedTask, transition, error) {
	result, err := p.queue.activateLegacy(
		ctx, &prepared.task, prepared.requestedReadyAtMS, prepared.legacyConfig, prepared.previousPayloadSHA256,
		operatorTransfer, request.FirstTime, p.owner,
	)
	if err == nil {
		return prepared, result, nil
	}
	var capacity queueCapacityError
	if errors.As(err, &capacity) {
		return prepared, result, capacity
	}
	var conflict queueConflictError
	if !errors.As(err, &conflict) || prepared.existingState != "ready" && prepared.existingState != "inflight" {
		return preparedTask{}, transition{}, producerQueueAuthority(err)
	}
	current, inspectErr := p.queue.inspect(ctx, request.PostingID)
	if inspectErr != nil || current == nil || (current.State != "terminal" && current.State != "dead") ||
		current.Task.PayloadSHA256 != prepared.task.PayloadSHA256 || !sameProducerIdentity(current.Task, prepared.task) {
		if inspectErr != nil {
			return preparedTask{}, transition{}, producerQueueAuthority(inspectErr)
		}
		return preparedTask{}, transition{}, producerQueueAuthority(err)
	}
	retryRequest := request
	retryRequest.Operation, retryRequest.ExpectedDigest = "prepare", ""
	retried, retryErr := p.prepareTask(ctx, retryRequest)
	if retryErr != nil || retried.legacy || (operatorTransfer && retried.digest != request.ExpectedDigest) {
		if retryErr != nil {
			return preparedTask{}, transition{}, retryErr
		}
		return preparedTask{}, transition{}, errProducerDigestMismatch
	}
	result, retryErr = p.queue.activateLegacy(
		ctx, &retried.task, retried.requestedReadyAtMS, retried.legacyConfig, retried.previousPayloadSHA256,
		operatorTransfer, request.FirstTime, p.owner,
	)
	if retryErr != nil {
		var retryCapacity queueCapacityError
		if errors.As(retryErr, &retryCapacity) {
			return retried, result, retryCapacity
		}
		return preparedTask{}, transition{}, producerQueueAuthority(retryErr)
	}
	return retried, result, nil
}

func (p *b0Producer) acquireMutationAuthority(ctx context.Context) (func(), error) {
	release, err := p.bootstrap.acquire(ctx)
	if err != nil {
		return nil, err
	}
	succeeded := false
	defer func() {
		if !succeeded {
			release()
		}
	}()
	// Startup and the two-second authority monitor perform full conservation
	// audits. Per-mutation authority needs the exact O(1) route/owner proof;
	// the Lua transition validates its target atomically.
	bootstrap, err := p.preflightLocked(ctx, false)
	if err != nil {
		return nil, err
	}
	if !bootstrap {
		if p.sentinel != nil {
			state, stateErr := p.sentinel.state()
			persist, stateErr := initializedSentinelNeedsPersistence(state, stateErr)
			if stateErr != nil {
				return nil, stateErr
			}
			if !persist {
				succeeded = true
				return release, nil
			}
			if err := p.sentinel.publishActive(); err != nil {
				return nil, authorityLost("corruption")
			}
		}
		succeeded = true
		return release, nil
	}
	if p.sentinel != nil {
		if err := p.sentinel.ensurePreparing(); err != nil {
			return nil, authorityLost("corruption")
		}
	}
	// The pending cutover receipt contains this lane until the wrapper saves
	// the owner and all transferred tasks to Redis before starting claimants.
	// A synchronous SAVE here cannot fit the producer request deadline.
	if err := p.queue.initializeProducer(ctx, p.owner); err != nil {
		return nil, producerQueueAuthority(err)
	}
	if p.sentinel != nil {
		if err := p.sentinel.publishActive(); err != nil {
			return nil, authorityLost("corruption")
		}
	}
	postBootstrap, err := p.preflightLocked(ctx, true)
	if err != nil || postBootstrap {
		if err == nil {
			err = authorityLost("corruption")
		}
		return nil, err
	}
	succeeded = true
	return release, nil
}

func initializedSentinelNeedsPersistence(state producerSentinelPhase, stateErr error) (bool, error) {
	if stateErr != nil {
		return false, authorityLost("corruption")
	}
	switch state {
	case producerSentinelActive:
		return false, nil
	case producerSentinelPreparing:
		return true, nil
	default:
		return false, authorityLost("corruption")
	}
}

var errProducerDigestMismatch = errors.New("producer preparation digest changed")

func producerQueueAuthority(err error) error {
	if errors.Is(err, context.Canceled) {
		return context.Canceled
	}
	if class, ok := authorityErrorClass(err); ok {
		return authorityLost(class)
	}
	return authorityLost("corruption")
}

type producerAssignmentIdentity struct {
	routingRevision string
	timeoutMS       int64
	scraperType     string
	wait            string
	waitFallback    *string
	digest          string
}

func producerAssignment(rawMetadata string) (map[string]any, producerAssignmentIdentity, error) {
	config, a, err := b0task.ResolveAssignment(rawMetadata)
	return config, producerAssignmentIdentity{routingRevision: a.RoutingRevision, timeoutMS: a.TimeoutMS, scraperType: a.ScraperType, wait: a.Wait, waitFallback: a.WaitFallback, digest: a.Digest}, err
}
func validNavigationWait(value string) bool                 { return b0task.ValidNavigationWait(value) }
func sameOptionalString(left, right *string) bool           { return b0task.SameOptionalString(left, right) }
func validateProducerDOMConfig(config map[string]any) error { return b0task.ValidateDOMConfig(config) }

func buildProducerTask(request producerRequest, route routeIdentity, parserConfig map[string]any, assignment producerAssignmentIdentity, revision int64) (queueTask, error) {
	envelope := map[string]any{
		"assignment_digest_sha256": assignment.digest, "board_id": request.Config["board_id"],
		"browser_backend": "lightpanda", "config_revision": revision, "domain": request.Domain,
		"engine_owner": engineOwner, "initial_ready_at_ms": request.NextScrapeAtMS,
		"parser_config": parserConfig, "policy_key": queuePolicyKey, "render": true,
		"routing_epoch": route.RoutingEpoch, "routing_revision": assignment.routingRevision,
		"schema_version": "lightpanda-b0-task-v1", "scraper_step": int64(0),
		"scraper_type": assignment.scraperType, "shard_id": route.ShardID,
		"source_url": request.Config["source_url"], "task_id": request.PostingID,
		"task_kind": "scrape", "timeout_ms": assignment.timeoutMS, "wait": assignment.wait, "wait_fallback": assignment.waitFallback,
	}
	payload, err := canonicalJSON(envelope, false)
	if err != nil || len(payload) == 0 || len(payload) > maxPayload {
		return queueTask{}, errors.New("canonical B0 task exceeds its bound")
	}
	digest := sha256.Sum256(payload)
	return decodeQueueTask(string(payload), hex.EncodeToString(digest[:]), route)
}

func sameProducerIdentity(current, wanted queueTask) bool {
	return current.Envelope.BoardID == wanted.Envelope.BoardID &&
		current.Envelope.SourceURL == wanted.Envelope.SourceURL &&
		current.Envelope.Domain == wanted.Envelope.Domain &&
		current.Envelope.AssignmentDigestSHA256 == wanted.Envelope.AssignmentDigestSHA256 &&
		current.Envelope.RoutingRevision == wanted.Envelope.RoutingRevision &&
		current.Envelope.TimeoutMS == wanted.Envelope.TimeoutMS &&
		current.Envelope.ScraperType == wanted.Envelope.ScraperType &&
		current.Envelope.Wait == wanted.Envelope.Wait && sameOptionalString(current.Envelope.WaitFallback, wanted.Envelope.WaitFallback)
}

func producerLegacyConfig(config map[string]string, task queueTask, scheduleScore string) (string, error) {
	normalized := make(map[string]any, len(config)+1)
	for key, value := range config {
		if !safeID.MatchString(key) {
			return "", errors.New("legacy config field is invalid")
		}
		normalized[key] = value
	}
	suppliedDomain, hasDomain := normalized["domain"]
	normalized["domain"] = task.Envelope.Domain
	if normalized["board_id"] != task.Envelope.BoardID || normalized["source_url"] != task.Envelope.SourceURL ||
		(hasDomain && suppliedDomain != task.Envelope.Domain) ||
		(normalized["scrape_step"] != nil && normalized["scrape_step"] != "0") {
		return "", errors.New("legacy config disagrees with B0 identity")
	}
	if scheduleScore != "" {
		// Operator-only CAS metadata is removed by Lua before validating the hash.
		normalized["__operator_source_score"] = scheduleScore
	}
	encoded, err := canonicalJSON(normalized, true)
	if err != nil || len(encoded) > maxPayload {
		return "", errors.New("legacy config exceeds its bound")
	}
	return string(encoded), nil
}

func canonicalInt(value any) (int64, bool) { return b0task.CanonicalInt(value) }
func keysWithin(values map[string]any, allowed map[string]struct{}) bool {
	return b0task.KeysWithin(values, allowed)
}
func parseCanonicalValue(raw []byte) (any, error) { return b0task.ParseCanonicalValue(raw) }
func canonicalJSON(value any, ensureASCII bool) ([]byte, error) {
	return b0task.CanonicalJSON(value, ensureASCII)
}
func canonicalNumber(raw string) (string, error) { return b0task.CanonicalNumber(raw) }
func appendCanonicalString(output []byte, value string, ensureASCII bool) ([]byte, error) {
	return b0task.AppendCanonicalString(output, value, ensureASCII)
}
func appendCanonicalJSON(output []byte, value any, ensureASCII bool) ([]byte, error) {
	return b0task.AppendCanonicalJSON(output, value, ensureASCII)
}
