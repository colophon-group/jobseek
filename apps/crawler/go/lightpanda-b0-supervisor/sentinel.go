package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"syscall"

	"github.com/redis/go-redis/v9"
)

const (
	producerSentinelSchema   = "jobseek.lightpanda.producer-activation/v1"
	producerSentinelMaxBytes = int64(4096)
)

type producerSentinelPhase string

const (
	producerSentinelAbsent    producerSentinelPhase = "absent"
	producerSentinelPreparing producerSentinelPhase = "preparing"
	producerSentinelActive    producerSentinelPhase = "active"
)

type producerActivationSentinel struct {
	mu        sync.Mutex
	path      string
	preparing []byte
	active    []byte
	owner     uint32
}

func newProducerActivationSentinel(path string, owner producerOwnerIdentity, uid uint32) (*producerActivationSentinel, error) {
	if path == "" || !filepath.IsAbs(path) || owner.validate() != nil {
		return nil, errors.New("invalid producer activation sentinel")
	}
	payload, err := canonicalJSON(map[string]any{
		"board_slugs":   owner.BoardSlugs,
		"cohort":        owner.Cohort,
		"engine_owner":  owner.Route.EngineOwner,
		"namespace":     owner.Namespace,
		"routing_epoch": owner.Route.RoutingEpoch,
		"schema":        producerSentinelSchema,
		"shard_id":      owner.Route.ShardID,
	}, true)
	if err != nil {
		return nil, errors.New("invalid producer activation sentinel content")
	}
	preparing := append([]byte{'P', '\n'}, payload...)
	preparing = append(preparing, '\n')
	active := append([]byte(nil), preparing...)
	active[0] = 'A'
	if len(preparing) > int(producerSentinelMaxBytes) || len(active) > int(producerSentinelMaxBytes) {
		return nil, errors.New("invalid producer activation sentinel content")
	}
	return &producerActivationSentinel{path: path, preparing: preparing, active: active, owner: uid}, nil
}

func (sentinel *producerActivationSentinel) state() (producerSentinelPhase, error) {
	sentinel.mu.Lock()
	defer sentinel.mu.Unlock()
	return sentinel.stateLocked()
}

func (sentinel *producerActivationSentinel) stateLocked() (producerSentinelPhase, error) {
	file, err := os.OpenFile(sentinel.path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if errors.Is(err, os.ErrNotExist) {
		return producerSentinelAbsent, nil
	}
	if err != nil {
		return "", errors.New("open producer activation sentinel")
	}
	defer file.Close()
	info, err := file.Stat()
	metadata, ok := infoSyscallStat(info)
	if err != nil || !ok || !safeProducerSentinelMetadata(info, metadata, sentinel.owner) {
		return "", errors.New("producer activation sentinel metadata is invalid")
	}
	payload, err := io.ReadAll(io.LimitReader(file, 4097))
	if err != nil {
		return "", errors.New("producer activation sentinel content is invalid")
	}
	switch {
	case bytes.Equal(payload, sentinel.preparing):
		return producerSentinelPreparing, nil
	case bytes.Equal(payload, sentinel.active):
		return producerSentinelActive, nil
	default:
		return "", errors.New("producer activation sentinel content is invalid")
	}
}

func (sentinel *producerActivationSentinel) isActive() (bool, error) {
	state, err := sentinel.state()
	return state == producerSentinelActive, err
}

func infoSyscallStat(info os.FileInfo) (*syscall.Stat_t, bool) {
	if info == nil {
		return nil, false
	}
	metadata, ok := info.Sys().(*syscall.Stat_t)
	return metadata, ok
}

func safeProducerSentinelMetadata(info os.FileInfo, metadata *syscall.Stat_t, owner uint32) bool {
	return info != nil && info.Mode().IsRegular() && info.Mode().Perm() == 0o600 &&
		metadata != nil && metadata.Uid == owner && metadata.Nlink == 1 &&
		info.Size() >= 0 && info.Size() <= producerSentinelMaxBytes
}

func (sentinel *producerActivationSentinel) ensurePreparing() error {
	sentinel.mu.Lock()
	defer sentinel.mu.Unlock()
	if state, err := sentinel.stateLocked(); err != nil || state == producerSentinelPreparing {
		return err
	} else if state != producerSentinelAbsent {
		return errors.New("producer activation sentinel is already active")
	}
	file, err := os.OpenFile(
		sentinel.path,
		os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW,
		0o600,
	)
	if err != nil {
		return errors.New("create producer activation sentinel")
	}
	if err := file.Chmod(0o600); err != nil {
		_ = file.Close()
		return errors.New("protect producer activation sentinel")
	}
	written, writeErr := file.Write(sentinel.preparing)
	if writeErr == nil && written != len(sentinel.preparing) {
		writeErr = io.ErrShortWrite
	}
	if writeErr == nil {
		writeErr = file.Sync()
	}
	closeErr := file.Close()
	if writeErr != nil || closeErr != nil {
		return errors.New("persist producer activation sentinel")
	}
	return syncProducerSentinelDirectory(filepath.Dir(sentinel.path))
}

func (sentinel *producerActivationSentinel) publishActive() error {
	sentinel.mu.Lock()
	defer sentinel.mu.Unlock()
	file, err := os.OpenFile(sentinel.path, os.O_RDWR|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return errors.New("open preparing producer activation sentinel")
	}
	defer file.Close()
	info, err := file.Stat()
	metadata, ok := infoSyscallStat(info)
	if err != nil || !ok || !safeProducerSentinelMetadata(info, metadata, sentinel.owner) {
		return errors.New("preparing producer activation sentinel metadata is invalid")
	}
	payload, err := io.ReadAll(io.LimitReader(file, 4097))
	if err != nil {
		return errors.New("read preparing producer activation sentinel")
	}
	if bytes.Equal(payload, sentinel.active) {
		return nil
	}
	if !bytes.Equal(payload, sentinel.preparing) {
		return errors.New("producer activation sentinel is not preparing")
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return errors.New("rewind preparing producer activation sentinel")
	}
	written, writeErr := file.Write([]byte{'A'})
	if writeErr == nil && written != 1 {
		writeErr = io.ErrShortWrite
	}
	if writeErr == nil {
		writeErr = file.Sync()
	}
	if writeErr != nil {
		return errors.New("persist active producer activation sentinel")
	}
	return syncProducerSentinelDirectory(filepath.Dir(sentinel.path))
}

func (sentinel *producerActivationSentinel) clear() error {
	sentinel.mu.Lock()
	defer sentinel.mu.Unlock()
	state, err := sentinel.stateLocked()
	if err != nil {
		return errors.New("producer activation sentinel is not exact")
	}
	if state == producerSentinelAbsent {
		return syncProducerSentinelDirectory(filepath.Dir(sentinel.path))
	}
	if state != producerSentinelActive {
		return errors.New("producer activation sentinel is not active")
	}
	if err := os.Remove(sentinel.path); err != nil {
		return errors.New("remove producer activation sentinel")
	}
	return syncProducerSentinelDirectory(filepath.Dir(sentinel.path))
}

func (sentinel *producerActivationSentinel) clearForRollback() error {
	sentinel.mu.Lock()
	defer sentinel.mu.Unlock()
	metadata, present, err := sentinel.rollbackIdentityLocked()
	if err != nil {
		return err
	}
	if !present {
		return syncProducerSentinelDirectory(filepath.Dir(sentinel.path))
	}
	current, err := os.Lstat(sentinel.path)
	currentMetadata, currentOK := infoSyscallStat(current)
	if err != nil || !currentOK || !safeProducerSentinelMetadata(current, currentMetadata, sentinel.owner) ||
		uint64(metadata.Dev) != uint64(currentMetadata.Dev) || uint64(metadata.Ino) != uint64(currentMetadata.Ino) {
		return errors.New("producer activation sentinel identity changed during rollback")
	}
	if err := os.Remove(sentinel.path); err != nil {
		return errors.New("remove producer activation sentinel")
	}
	return syncProducerSentinelDirectory(filepath.Dir(sentinel.path))
}

func (sentinel *producerActivationSentinel) clearableForRollback() error {
	sentinel.mu.Lock()
	defer sentinel.mu.Unlock()
	_, _, err := sentinel.rollbackIdentityLocked()
	return err
}

func (sentinel *producerActivationSentinel) rollbackIdentityLocked() (*syscall.Stat_t, bool, error) {
	file, err := os.OpenFile(sentinel.path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if errors.Is(err, os.ErrNotExist) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, errors.New("unsafe producer activation sentinel during rollback")
	}
	info, statErr := file.Stat()
	metadata, ok := infoSyscallStat(info)
	payload, readErr := io.ReadAll(io.LimitReader(file, producerSentinelMaxBytes+1))
	closeErr := file.Close()
	if statErr != nil || readErr != nil || closeErr != nil || !ok ||
		!safeProducerSentinelMetadata(info, metadata, sentinel.owner) ||
		!sentinel.safeRecoveryPayload(payload) {
		return nil, false, errors.New("unsafe producer activation sentinel during rollback")
	}
	return metadata, true, nil
}

func (sentinel *producerActivationSentinel) safeRecoveryPayload(payload []byte) bool {
	return bytes.Equal(payload, sentinel.preparing) || bytes.Equal(payload, sentinel.active) ||
		(len(payload) < len(sentinel.preparing) && bytes.Equal(payload, sentinel.preparing[:len(payload)]))
}

func syncProducerSentinelDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return errors.New("open producer sentinel directory")
	}
	defer directory.Close()
	if err := directory.Sync(); err != nil {
		return errors.New("persist producer sentinel directory")
	}
	return nil
}

func producerOwnerFromConfig(configured producerConfig) (producerOwnerIdentity, error) {
	cohort, ok := producerCohorts[configured.Cohort]
	if !ok {
		return producerOwnerIdentity{}, errors.New("invalid producer cohort")
	}
	slugs := make([]string, 0, len(cohort))
	for slug := range cohort {
		slugs = append(slugs, slug)
	}
	sort.Strings(slugs)
	owner := producerOwnerIdentity{
		Namespace: configured.Namespace, Cohort: configured.Cohort, Route: configured.Route, BoardSlugs: slugs,
	}
	return owner, owner.validate()
}

func rollbackSentinel(configured producerConfig) (*producerActivationSentinel, error) {
	owner, err := producerOwnerFromConfig(configured)
	if err != nil {
		return nil, err
	}
	sentinel, err := newProducerActivationSentinel(producerSentinelPath, owner, uint32(os.Geteuid()))
	if err != nil {
		return nil, err
	}
	if filepath.Dir(configured.Socket) != filepath.Dir(sentinel.path) {
		return nil, fmt.Errorf("producer sentinel directory disagrees with socket for UID %s", strconv.Itoa(os.Geteuid()))
	}
	if err := validateProducerSocketDirectory(filepath.Dir(sentinel.path), uint32(os.Geteuid())); err != nil {
		return nil, err
	}
	return sentinel, nil
}

func checkProducerActivationSentinelClearable(configured producerConfig) error {
	sentinel, err := rollbackSentinel(configured)
	if err != nil {
		return err
	}
	return sentinel.clearableForRollback()
}

func checkProducerActivationSentinelAbsent(configured producerConfig) error {
	sentinel, err := rollbackSentinel(configured)
	if err != nil {
		return err
	}
	state, err := sentinel.state()
	if err != nil || state != producerSentinelAbsent {
		return errors.New("producer activation sentinel is not absent")
	}
	return nil
}

func clearProducerActivationSentinel(configured producerConfig, rollbackPlanDigest, sourceReceiptSHA256 string) error {
	if !hex256.MatchString(rollbackPlanDigest) || !hex256.MatchString(sourceReceiptSHA256) {
		return errors.New("rollback tombstone cycle identity is invalid")
	}
	sentinel, err := rollbackSentinel(configured)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), producerTimeout)
	defer cancel()
	client := redis.NewClient(configured.RedisOptions)
	defer client.Close()
	tag := "lightpanda-b0:{" + configured.Namespace + "}"
	keys := []string{
		tag + ":route", tag + ":records", tag + ":ready", tag + ":inflight",
		tag + ":dead", tag + ":terminal", tag + ":origin-holders", legacyGuardKey,
	}
	pipeline := client.Pipeline()
	types := make([]*redis.StatusCmd, 0, len(keys))
	for _, key := range keys {
		types = append(types, pipeline.Type(ctx, key))
	}
	if _, err := pipeline.Exec(ctx); err != nil {
		return errors.New("verify released producer Redis authority")
	}
	for _, keyType := range types {
		if keyType.Val() != "none" {
			return errors.New("producer Redis authority is not released")
		}
	}
	owner, err := client.HGetAll(ctx, producerOwnerKey).Result()
	if err != nil {
		return errors.New("verify rollback tombstone")
	}
	expected := map[string]string{
		"schema": "jobseek.lightpanda.producer-rollback/v1", "namespace": configured.Namespace,
		"shard_id": configured.Route.ShardID, "routing_epoch": strconv.FormatInt(configured.Route.RoutingEpoch, 10),
		"engine_owner": "go", "cohort": configured.Cohort,
		"rollback_plan_digest": rollbackPlanDigest, "source_receipt_sha256": sourceReceiptSHA256,
	}
	if len(owner) != len(expected) {
		return errors.New("rollback tombstone is not exact")
	}
	for field, value := range expected {
		if owner[field] != value {
			return errors.New("rollback tombstone is not exact")
		}
	}
	if err := removeStaleProducerSocket(ctx, configured.Socket, uint32(os.Geteuid())); err != nil {
		return err
	}
	return sentinel.clearForRollback()
}

func removeStaleProducerSocket(ctx context.Context, path string, owner uint32) error {
	if ctx == nil {
		return errors.New("producer socket reset context is required")
	}
	probeContext, cancelProbe := context.WithTimeout(ctx, producerTimeout)
	defer cancelProbe()
	if _, err := os.Lstat(path); errors.Is(err, os.ErrNotExist) {
		return nil
	} else if err != nil {
		return errors.New("inspect producer socket during sentinel reset")
	}
	before, err := validateProducerSocket(path, owner)
	if err != nil {
		return errors.New("unsafe producer socket during sentinel reset")
	}
	connection, dialErr := (&net.Dialer{}).DialContext(probeContext, "unix", path)
	if dialErr == nil {
		_ = connection.Close()
		return errors.New("live producer socket during sentinel reset")
	}
	if probeContext.Err() != nil || !errors.Is(dialErr, syscall.ECONNREFUSED) {
		return errors.New("producer socket liveness could not be disproved")
	}
	after, err := validateProducerSocket(path, owner)
	if err != nil || after != before {
		return errors.New("producer socket identity changed during sentinel reset")
	}
	if err := os.Remove(path); err != nil {
		return errors.New("remove stale producer socket")
	}
	return syncProducerSentinelDirectory(filepath.Dir(path))
}
