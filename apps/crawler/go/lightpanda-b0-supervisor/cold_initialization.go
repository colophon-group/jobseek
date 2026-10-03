package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
)

// The ADR006 host coordinator independently verifies the retained SQL decision,
// immutable release and all-writer quiescence before delegating this root-owned
// request. Its hashes are identities, not substitutes for those host checks.
// The producer receives no database credentials and establishes no task/owner.
type producerColdDecision struct {
	AllWritersReceiptSHA256       string `json:"all_writers_receipt_sha256"`
	Cohort                        string `json:"cohort"`
	LuaSHA256                     string `json:"lua_sha256"`
	Namespace                     string `json:"namespace"`
	OrdinaryRestorationPlanSHA256 string `json:"ordinary_restoration_plan_sha256"`
	ReversalSHA256                string `json:"reversal_sha256"`
	RoutingEpoch                  int64  `json:"routing_epoch"`
	RuntimeImage                  string `json:"runtime_image"`
	Schema                        string `json:"schema"`
	ShardID                       string `json:"shard_id"`
	SourceEpoch                   int64  `json:"source_epoch"`
	SourceRevision                string `json:"source_revision"`
}

const producerColdSchema = "jobseek.lightpanda.producer-cold-initialization/v1"

// Set only by the immutable image build, as for the ordinary worker.
var sourceRevision string

func decodeProducerColdDecision(body []byte, digest string, configured producerConfig, source string) error {
	sum := sha256.Sum256(body)
	if len(body) == 0 || len(body) > 8192 || !hex256.MatchString(digest) || hex.EncodeToString(sum[:]) != digest {
		return errors.New("cold producer decision digest mismatch")
	}
	var d producerColdDecision
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&d) != nil {
		return errors.New("invalid cold producer decision")
	}
	canonical, err := json.Marshal(d)
	if err != nil || !bytes.Equal(body, canonical) || d.Schema != producerColdSchema ||
		!hex160.MatchString(source) || d.SourceRevision != source || d.Cohort != configured.Cohort ||
		d.Namespace != configured.Namespace || d.ShardID != configured.Route.ShardID ||
		d.RoutingEpoch != configured.Route.RoutingEpoch || d.SourceEpoch < 1 || d.SourceEpoch >= d.RoutingEpoch ||
		d.LuaSHA256 != expectedLuaSHA256 || !hex256.MatchString(d.AllWritersReceiptSHA256) ||
		!hex256.MatchString(d.OrdinaryRestorationPlanSHA256) || !hex256.MatchString(d.ReversalSHA256) ||
		!strings.HasPrefix(d.RuntimeImage, "ghcr.io/colophon-group/jobseek-crawler@sha256:") ||
		!hex256.MatchString(strings.TrimPrefix(d.RuntimeImage, "ghcr.io/colophon-group/jobseek-crawler@sha256:")) {
		return errors.New("cold producer decision does not match fixed runtime identity")
	}
	return nil
}

func readProducerColdFile(path string, owner uint32, delegated bool) ([]byte, error) {
	if !filepath.IsAbs(path) {
		return nil, errors.New("cold producer file must be absolute")
	}
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, errors.New("open protected cold producer file")
	}
	defer file.Close()
	info, err := file.Stat()
	stat, ok := infoSyscallStat(info)
	mode := os.FileMode(0600)
	if delegated {
		mode = 0640
	}
	if err != nil || !ok || !info.Mode().IsRegular() || info.Mode().Perm() != mode ||
		stat.Uid != owner || stat.Nlink != 1 || info.Size() <= 0 || info.Size() > 8192 ||
		(delegated && stat.Gid != uint32(os.Getegid())) {
		return nil, errors.New("unsafe cold producer file metadata")
	}
	body, err := io.ReadAll(io.LimitReader(file, 8193))
	current, currentErr := os.Lstat(path)
	currentStat, currentOK := infoSyscallStat(current)
	if err != nil || currentErr != nil || !currentOK || currentStat.Dev != stat.Dev || currentStat.Ino != stat.Ino || len(body) != int(info.Size()) {
		return nil, errors.New("cold producer file identity changed")
	}
	return body, nil
}

func retainProducerColdFile(path string, body []byte) error {
	// Publication is atomic. Interrupted temporary files carry no authority.
	directory := filepath.Dir(path)
	file, err := os.CreateTemp(directory, ".cold-initialization-tmp-")
	if err != nil {
		return errors.New("create cold producer history")
	}
	name := file.Name()
	defer os.Remove(name)
	if err := file.Chmod(0600); err != nil {
		_ = file.Close()
		return errors.New("protect cold producer history")
	}
	written, err := file.Write(body)
	if err == nil && written != len(body) {
		err = io.ErrShortWrite
	}
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil || closeErr != nil {
		return errors.New("persist cold producer history")
	}
	// The exclusive lifecycle lock and prior absence check prevent replacement.
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		return errors.New("cold producer history already exists")
	}
	if err := os.Rename(name, path); err != nil {
		return errors.New("publish cold producer history")
	}
	return syncProducerSentinelDirectory(directory)
}

// Shared by the serving producer and one-shot initialization. There can be no
// task activation between its empty-queue check and persisted completion.
func acquireProducerLifecycleLock(directory string) (func(), error) {
	if err := validateProducerSocketDirectory(directory, uint32(os.Geteuid())); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(filepath.Join(directory, ".lifecycle-v1.lock"), os.O_RDWR|os.O_CREATE|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0600)
	if err != nil {
		return nil, errors.New("open producer lifecycle lock")
	}
	info, err := file.Stat()
	stat, ok := infoSyscallStat(info)
	if err != nil || !ok || !safeProducerSentinelMetadata(info, stat, uint32(os.Geteuid())) || info.Size() != 0 {
		_ = file.Close()
		return nil, errors.New("unsafe producer lifecycle lock")
	}
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = file.Close()
		return nil, errors.New("producer lifecycle is already active")
	}
	return func() { _ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN); _ = file.Close() }, nil
}

func runProducerColdInitialization(configured producerConfig, path, digest string) error {
	if os.Geteuid() != 10001 || configured.ClientUID != 0 || configured.Socket != producerSocketPath || configured.RedisOptions == nil {
		return errors.New("cold producer initialization requires fixed UID 10001 and root coordinator")
	}
	body, err := readProducerColdFile(path, 0, true)
	if err != nil {
		return err
	}
	if err := decodeProducerColdDecision(body, digest, configured, sourceRevision); err != nil {
		return err
	}
	release, err := acquireProducerLifecycleLock(filepath.Dir(configured.Socket))
	if err != nil {
		return err
	}
	defer release()
	if _, err := os.Lstat(configured.Socket); !os.IsNotExist(err) {
		return errors.New("cold producer initialization requires absent producer socket")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := redis.NewClient(configured.RedisOptions)
	defer client.Close()
	queue, err := newB0Queue(client, configured.LuaPath, configured.Namespace, configured.Route, configured.DefaultDelay, newMetrics())
	if err != nil {
		return err
	}
	producer, err := newB0Producer(client, queue, configured.Cohort, configured.Route)
	if err != nil {
		return err
	}
	prefix := filepath.Join(filepath.Dir(configured.Socket), ".cold-initialization-v1-"+digest)
	return initializeColdProducer(ctx, producer, body, prefix, func(ctx context.Context) error {
		reply, err := client.Save(ctx).Result()
		if err != nil || reply != "OK" {
			return errors.New("cold producer SAVE not acknowledged")
		}
		return nil
	})
}

func initializeColdProducer(ctx context.Context, p *b0Producer, body []byte, prefix string, save func(context.Context) error) error {
	if ctx == nil || p == nil || p.sentinel == nil || save == nil {
		return errors.New("invalid cold producer initialization")
	}
	requestPath, completionPath := prefix+".request", prefix+".complete"
	retained, err := readProducerColdFile(requestPath, uint32(os.Geteuid()), false)
	if err != nil {
		if _, absent := os.Lstat(requestPath); !os.IsNotExist(absent) {
			return err
		}
		// Missing history cannot be reconstructed from an existing authority pair.
		fresh, err := p.preflightLocked(ctx, true)
		state, stateErr := p.sentinel.state()
		if err != nil || stateErr != nil || !fresh || state != producerSentinelAbsent {
			return errors.New("cold producer history absent for existing authority")
		}
		if _, err := os.Lstat(completionPath); !os.IsNotExist(err) {
			return errors.New("cold producer completion lacks request")
		}
		if err := retainProducerColdFile(requestPath, body); err != nil {
			return err
		}
	} else if !bytes.Equal(retained, body) {
		return errors.New("cold producer retained decision changed")
	}
	if err := syncProducerSentinelDirectory(filepath.Dir(requestPath)); err != nil {
		return err
	}
	complete, err := readProducerColdFile(completionPath, uint32(os.Geteuid()), false)
	if err == nil {
		if !bytes.Equal(complete, body) {
			return errors.New("cold producer completion changed")
		}
		if err := auditEmptyColdProducer(ctx, p, true); err != nil {
			return err
		}
		return syncProducerSentinelDirectory(filepath.Dir(completionPath))
	}
	if _, absent := os.Lstat(completionPath); !os.IsNotExist(absent) {
		return err
	}
	// Full source audit before bootstrap; recovery only accepts the normal exact
	// preparing/active pair. Orphan Redis and lost sentinel still fail closed.
	if err := auditEmptyColdProducer(ctx, p, false); err != nil {
		return err
	}
	release, err := p.acquireMutationAuthority(ctx)
	if err != nil {
		return err
	}
	defer release()
	if err := auditEmptyColdProducer(ctx, p, true); err != nil {
		return err
	}
	if err := save(ctx); err != nil {
		return err
	}
	if err := auditEmptyColdProducer(ctx, p, true); err != nil {
		return err
	}
	return retainProducerColdFile(completionPath, body)
}

func auditEmptyColdProducer(ctx context.Context, p *b0Producer, initialized bool) error {
	fresh, err := p.preflightLocked(ctx, true)
	if err != nil || (initialized && fresh) {
		return errors.New("cold producer authority readback failed")
	}
	occupancy, err := p.queue.lifetimeOccupancy(ctx)
	if err != nil || occupancy != 0 {
		return errors.New("cold producer initialization requires empty lifetime queue")
	}
	if initialized {
		state, err := p.sentinel.state()
		if err != nil || state != producerSentinelActive {
			return errors.New("cold producer sentinel readback failed")
		}
	}
	return nil
}
