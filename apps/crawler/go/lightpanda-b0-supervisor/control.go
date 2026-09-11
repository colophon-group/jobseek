package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"syscall"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	"github.com/redis/go-redis/v9"
)

type producerSocketIdentity struct {
	device uint64
	inode  uint64
}

type producerConnections struct {
	mu          sync.Mutex
	connections map[*net.UnixConn]struct{}
	closed      bool
}

func newProducerConnections() *producerConnections {
	return &producerConnections{connections: make(map[*net.UnixConn]struct{})}
}

func (active *producerConnections) add(connection *net.UnixConn) bool {
	active.mu.Lock()
	defer active.mu.Unlock()
	if active.closed {
		_ = connection.Close()
		return false
	}
	active.connections[connection] = struct{}{}
	return true
}

func (active *producerConnections) remove(connection *net.UnixConn) {
	active.mu.Lock()
	delete(active.connections, connection)
	active.mu.Unlock()
}

func (active *producerConnections) closeAll() {
	active.mu.Lock()
	active.closed = true
	for connection := range active.connections {
		_ = connection.Close()
	}
	active.mu.Unlock()
}

type producerAuthorityLoss struct {
	class     string
	operation string
}

func (failure producerAuthorityLoss) Error() string {
	return "producer authority lost: " + failure.class + "/" + failure.operation
}

type producerAuthorityLatch struct {
	once   sync.Once
	cancel context.CancelFunc
	lost   chan producerAuthorityLoss
}

func newProducerAuthorityLatch(cancel context.CancelFunc) *producerAuthorityLatch {
	return &producerAuthorityLatch{cancel: cancel, lost: make(chan producerAuthorityLoss, 1)}
}

func (latch *producerAuthorityLatch) trip(class, operation string) {
	if latch == nil {
		return
	}
	latch.once.Do(func() {
		if !contains(set("redis", "fenced", "corruption"), class) {
			class = "corruption"
		}
		if !contains(set("preflight", "health", "prepare", "enqueue", "activate"), operation) {
			operation = "unknown"
		}
		latch.lost <- producerAuthorityLoss{class: class, operation: operation}
		latch.cancel()
	})
}

func (latch *producerAuthorityLatch) failure() (producerAuthorityLoss, bool) {
	select {
	case failure := <-latch.lost:
		latch.lost <- failure
		return failure, true
	default:
		return producerAuthorityLoss{}, false
	}
}

func runProducer(ctx context.Context, configured producerConfig) error {
	if ctx == nil || configured.RedisOptions == nil || configured.Socket != producerSocketPath {
		return errors.New("invalid B0 producer runtime")
	}
	client := redis.NewClient(configured.RedisOptions)
	defer client.Close()
	queue, err := newB0Queue(
		client, configured.LuaPath, configured.Namespace, configured.Route,
		configured.DefaultDelay, newMetrics(),
	)
	if err != nil {
		return err
	}
	producer, err := newB0Producer(client, queue, configured.Cohort, configured.Route)
	if err != nil {
		return err
	}
	return serveProducer(ctx, configured.Socket, producer, configured.ClientUID)
}

func serveProducer(ctx context.Context, path string, producer *b0Producer, acceptedUID uint32) error {
	if ctx == nil || producer == nil || path != producerSocketPath || acceptedUID == uint32(os.Geteuid()) {
		return errors.New("invalid producer control server")
	}
	return serveProducerAt(ctx, path, producer, acceptedUID)
}

func serveProducerAt(ctx context.Context, path string, producer *b0Producer, acceptedUID uint32) error {
	if ctx == nil || producer == nil || path == "" || !filepath.IsAbs(path) {
		return errors.New("invalid producer control server")
	}
	if err := preflightProducerAuthority(ctx, producer, true); err != nil {
		if ctx.Err() != nil {
			return nil
		}
		return err
	}
	if err := validateProducerSocketDirectory(filepath.Dir(path), uint32(os.Geteuid())); err != nil {
		return err
	}
	if _, err := os.Lstat(path); err == nil {
		if err := removeStaleProducerSocket(ctx, path, uint32(os.Geteuid())); err != nil {
			return fmt.Errorf("refuse existing producer socket: %w", err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("inspect producer socket: %w", err)
	}
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		return fmt.Errorf("listen on producer control socket: %w", err)
	}
	defer listener.Close()
	raw, err := listener.SyscallConn()
	if err != nil {
		return fmt.Errorf("bound producer socket backlog: %w", err)
	}
	var listenErr error
	if err := raw.Control(func(descriptor uintptr) {
		listenErr = syscall.Listen(int(descriptor), producerBacklog)
	}); err != nil || listenErr != nil {
		return errors.New("bound producer socket backlog failed")
	}
	if err := os.Chmod(path, 0o600); err != nil {
		return fmt.Errorf("protect producer control socket: %w", err)
	}
	identity, err := validateProducerSocket(path, uint32(os.Geteuid()))
	if err != nil {
		return err
	}
	defer removeProducerSocket(path, identity)

	authorityContext, cancelAuthority := context.WithCancel(ctx)
	defer cancelAuthority()
	latch := newProducerAuthorityLatch(cancelAuthority)
	handlers := make(chan struct{}, producerMaxHandlers)
	var active sync.WaitGroup
	connections := newProducerConnections()
	stop := context.AfterFunc(authorityContext, func() { _ = listener.Close() })
	defer stop()
	stopConnections := context.AfterFunc(authorityContext, connections.closeAll)
	defer stopConnections()
	active.Add(1)
	go func() {
		defer active.Done()
		monitorProducerAuthority(authorityContext, producer, latch)
	}()
	for {
		select {
		case handlers <- struct{}{}:
		case <-authorityContext.Done():
			return finishProducerServer(ctx, latch, &active)
		}
		connection, acceptErr := listener.AcceptUnix()
		if acceptErr != nil {
			<-handlers
			if authorityContext.Err() != nil {
				return finishProducerServer(ctx, latch, &active)
			}
			return fmt.Errorf("accept producer control connection: %w", acceptErr)
		}
		if !connections.add(connection) {
			<-handlers
			continue
		}
		active.Add(1)
		go func() {
			defer active.Done()
			defer func() { <-handlers }()
			defer connections.remove(connection)
			handleProducerConnection(connection, producer, acceptedUID, authorityContext, latch)
		}()
	}
}

func preflightProducerAuthority(ctx context.Context, producer *b0Producer, full bool) error {
	preflightContext, cancel := context.WithTimeout(ctx, producerTimeout)
	defer cancel()
	if err := producer.preflight(preflightContext, full); err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if class, ok := authorityErrorClass(err); ok {
			return producerAuthorityLoss{class: class, operation: "preflight"}
		}
		return producerAuthorityLoss{class: "corruption", operation: "preflight"}
	}
	return nil
}

func monitorProducerAuthority(ctx context.Context, producer *b0Producer, latch *producerAuthorityLatch) {
	ticker := time.NewTicker(producerPreflightInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := preflightProducerAuthority(ctx, producer, true); err != nil {
				if ctx.Err() != nil {
					return
				}
				var failure producerAuthorityLoss
				if !errors.As(err, &failure) {
					failure = producerAuthorityLoss{class: "corruption", operation: "preflight"}
				}
				latch.trip(failure.class, failure.operation)
				return
			}
		}
	}
}

func finishProducerServer(parent context.Context, latch *producerAuthorityLatch, active *sync.WaitGroup) error {
	done := make(chan struct{})
	go func() {
		active.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(producerTimeout):
		if failure, ok := latch.failure(); ok {
			return failure
		}
		return errors.New("producer control handlers did not stop")
	}
	if failure, ok := latch.failure(); ok {
		return failure
	}
	if parent.Err() != nil {
		return nil
	}
	return errors.New("producer control server stopped without cancellation")
}

func removeProducerSocket(path string, expected producerSocketIdentity) {
	current, err := validateProducerSocket(path, uint32(os.Geteuid()))
	if err == nil && current == expected {
		_ = os.Remove(path)
	}
}

func handleProducerConnection(
	connection *net.UnixConn,
	producer *b0Producer,
	acceptedUID uint32,
	authorityContext context.Context,
	latch *producerAuthorityLatch,
) {
	defer connection.Close()
	uid, err := producerPeerUID(connection)
	healthUID := uint32(os.Geteuid())
	if err != nil || (uid != acceptedUID && uid != healthUID) {
		return
	}
	_ = connection.SetDeadline(time.Now().Add(producerTimeout))
	payload, err := framing.ReadRecord(connection, producerFrameLimit)
	if err != nil {
		return
	}
	request, err := decodeProducerRequest(payload)
	if err != nil {
		_ = writeProducerResponse(connection, producerFailure("request_invalid"))
		return
	}
	requestContext, cancel := context.WithTimeout(authorityContext, producerTimeout)
	defer cancel()
	response := producerFailure("request_invalid")
	var operationErr error
	if request.Operation == "health" {
		if uid != healthUID {
			_ = writeProducerResponse(connection, response)
			return
		}
		operationErr = producer.health(requestContext, request, acceptedUID)
		if operationErr == nil {
			response = responseForHealth()
		}
	} else if uid != acceptedUID {
		return
	} else if request.Operation == "manifest" {
		slugs, manifestErr := producer.manifest(request)
		operationErr = manifestErr
		if manifestErr == nil {
			response = responseForManifest(producer.cohortName, slugs)
		}
	} else if request.Operation == "prepare" {
		prepared, prepareErr := producer.prepare(requestContext, request)
		operationErr = prepareErr
		if prepareErr == nil {
			response = responseForPreparation(prepared)
		}
	} else if request.Operation == "enqueue" {
		prepared, result, enqueueErr := producer.enqueue(requestContext, request)
		operationErr = enqueueErr
		if enqueueErr == nil {
			if prepared.legacy {
				response = responseForPreparation(prepared)
			} else {
				response = responseForActivation(prepared, result)
			}
		}
	} else if request.Operation == "activate" {
		prepared, result, activateErr := producer.activate(requestContext, request)
		operationErr = activateErr
		if activateErr == nil {
			response = responseForActivation(prepared, result)
		} else if errors.Is(activateErr, errProducerDigestMismatch) {
			response = producerFailure("digest_mismatch")
		}
	}
	var capacityFailure queueCapacityError
	if errors.As(operationErr, &capacityFailure) {
		response = responseForCapacity(capacityFailure)
		operationErr = nil
	} else if operationErr == nil {
		occupancy, capacityErr := producer.queue.lifetimeOccupancy(requestContext)
		if capacityErr != nil {
			operationErr = producerQueueAuthority(capacityErr)
		} else {
			response.LifetimeOccupancy = occupancy
			response.LifetimeCapacity = queueRecordLimit
			response.LifetimeHeadroom = queueRecordLimit - occupancy
		}
	}
	lostClass, lostAuthority := authorityErrorClass(operationErr)
	if lostAuthority && authorityContext.Err() == nil {
		response = producerFailure("authority_lost")
	}
	_ = writeProducerResponse(connection, response)
	if lostAuthority && authorityContext.Err() == nil {
		latch.trip(lostClass, request.Operation)
	}
}

func decodeProducerRequest(payload []byte) (producerRequest, error) {
	value, err := parseCanonicalValue(payload)
	if err != nil {
		return producerRequest{}, err
	}
	object, ok := value.(map[string]any)
	if !ok || len(object) != 11 {
		return producerRequest{}, errors.New("producer request fields are not exact")
	}
	for _, field := range []string{
		"version", "operation", "cohort", "domain", "posting_id", "next_scrape_at_ms",
		"config", "browser", "first_time", "operator_transfer", "expected_digest",
	} {
		if _, present := object[field]; !present {
			return producerRequest{}, errors.New("producer request fields are not exact")
		}
	}
	canonical, err := canonicalJSON(value, true)
	if err != nil || !bytes.Equal(canonical, payload) {
		return producerRequest{}, errors.New("producer request is not canonical JSON")
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	var request producerRequest
	if err := decoder.Decode(&request); err != nil {
		return producerRequest{}, err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return producerRequest{}, errors.New("producer request has trailing JSON")
	}
	return request, nil
}

func responseForPreparation(prepared preparedTask) producerResponse {
	if prepared.legacy {
		return producerResponse{
			Version: producerProtocol, Outcome: "legacy", Reason: "literal_legacy",
			BoardSlugs: []string{},
		}
	}
	return producerResponse{
		Version: producerProtocol, Outcome: "prepared", Reason: "prepared",
		PreparationDigest: prepared.digest, PayloadSHA256: prepared.task.PayloadSHA256,
		ExistingState: prepared.existingState, ExistingPayloadSHA256: prepared.existingPayload,
		BoardSlugs: []string{},
	}
}

func responseForActivation(prepared preparedTask, result transition) producerResponse {
	return producerResponse{
		Version: producerProtocol, Outcome: "activated", Reason: result.Reason,
		PreparationDigest: prepared.digest, PayloadSHA256: prepared.task.PayloadSHA256,
		ExistingState: prepared.existingState, ExistingPayloadSHA256: prepared.existingPayload,
		Activated:  result.Reason == "activated" || result.Reason == "reactivated",
		BoardSlugs: []string{},
	}
}

func responseForManifest(cohort string, boardSlugs []string) producerResponse {
	return producerResponse{
		Version: producerProtocol, Outcome: "manifest", Reason: "manifest",
		Cohort: cohort, BoardSlugs: boardSlugs,
	}
}

func responseForHealth() producerResponse {
	return producerResponse{
		Version: producerProtocol, Outcome: "healthy", Reason: "authority_current",
		BoardSlugs: []string{},
	}
}

func producerFailure(reason string) producerResponse {
	return producerResponse{
		Version: producerProtocol, Outcome: "error", Reason: reason, BoardSlugs: []string{},
		LifetimeCapacity: queueRecordLimit, LifetimeHeadroom: queueRecordLimit,
	}
}

func responseForCapacity(failure queueCapacityError) producerResponse {
	return producerResponse{
		Version: producerProtocol, Outcome: "capacity", Reason: failure.Reason, BoardSlugs: []string{},
		LifetimeOccupancy: failure.Occupancy, LifetimeCapacity: failure.Capacity,
		LifetimeHeadroom: failure.Capacity - failure.Occupancy,
	}
}

func writeProducerResponse(output io.Writer, response producerResponse) error {
	payload, err := canonicalJSON(response, true)
	if err != nil {
		return err
	}
	return writeProducerRecord(output, payload)
}

func writeProducerRecord(output io.Writer, payload []byte) error {
	record, err := framing.EncodeRecord(payload, producerFrameLimit)
	if err != nil {
		return err
	}
	for len(record) != 0 {
		written, writeErr := output.Write(record)
		if writeErr != nil {
			return writeErr
		}
		if written <= 0 {
			return io.ErrShortWrite
		}
		record = record[written:]
	}
	return nil
}

func validateProducerSocketDirectory(path string, owner uint32) error {
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0o700 {
		return errors.New("producer socket directory is not private")
	}
	metadata, ok := info.Sys().(*syscall.Stat_t)
	if !ok || metadata.Uid != owner {
		return errors.New("producer socket directory owner is invalid")
	}
	return nil
}

func validateProducerSocket(path string, owner uint32) (producerSocketIdentity, error) {
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSocket == 0 || info.Mode().Perm() != 0o600 {
		return producerSocketIdentity{}, errors.New("producer socket metadata is invalid")
	}
	metadata, ok := info.Sys().(*syscall.Stat_t)
	if !ok || metadata.Uid != owner {
		return producerSocketIdentity{}, errors.New("producer socket owner is invalid")
	}
	return producerSocketIdentity{device: uint64(metadata.Dev), inode: uint64(metadata.Ino)}, nil
}

func checkProducerReady(path string, owner, acceptedUID uint32) error {
	if err := validateProducerSocketDirectory(filepath.Dir(path), owner); err != nil {
		return err
	}
	before, err := validateProducerSocket(path, owner)
	if err != nil {
		return err
	}
	dialer := net.Dialer{Timeout: producerTimeout}
	rawConnection, err := dialer.Dial("unix", path)
	if err != nil {
		return err
	}
	connection, ok := rawConnection.(*net.UnixConn)
	if !ok {
		_ = rawConnection.Close()
		return errors.New("producer readiness connection is not Unix")
	}
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(producerTimeout))
	if peerUID, err := producerPeerUID(connection); err != nil || peerUID != owner {
		return errors.New("producer readiness peer identity is invalid")
	}
	after, err := validateProducerSocket(path, owner)
	if err != nil || before != after || validateProducerSocketDirectory(filepath.Dir(path), owner) != nil {
		return errors.New("producer socket identity changed during readiness")
	}
	request := producerRequest{
		Version: producerProtocol, Operation: "health",
		Config: map[string]string{"client_uid": strconv.FormatUint(uint64(acceptedUID), 10)},
	}
	payload, err := canonicalJSON(request, true)
	if err != nil {
		return err
	}
	if err := writeProducerRecord(connection, payload); err != nil {
		return err
	}
	response, err := framing.ReadRecord(connection, producerFrameLimit)
	if err != nil {
		return err
	}
	canonical, err := parseCanonicalValue(response)
	if err != nil {
		return errors.New("producer readiness protocol failed")
	}
	reencoded, err := canonicalJSON(canonical, true)
	if err != nil || !bytes.Equal(response, reencoded) {
		return errors.New("producer readiness protocol failed")
	}
	decoder := json.NewDecoder(bytes.NewReader(response))
	decoder.DisallowUnknownFields()
	var health producerResponse
	if decoder.Decode(&health) != nil || health.Version != producerProtocol || health.Outcome != "healthy" ||
		health.Reason != "authority_current" || health.Activated || health.Cohort != "" || len(health.BoardSlugs) != 0 ||
		health.PreparationDigest != "" || health.PayloadSHA256 != "" || health.ExistingState != "" ||
		health.ExistingPayloadSHA256 != "" || health.LifetimeCapacity != queueRecordLimit ||
		health.LifetimeOccupancy < 0 || health.LifetimeOccupancy > health.LifetimeCapacity ||
		health.LifetimeHeadroom != health.LifetimeCapacity-health.LifetimeOccupancy {
		return errors.New("producer readiness protocol failed")
	}
	trailing := make([]byte, 1)
	if count, err := connection.Read(trailing); count != 0 || !errors.Is(err, io.EOF) {
		return errors.New("producer readiness response was not exact")
	}
	return nil
}
