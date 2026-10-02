package queue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// The CDC marker is also used by migration 0012 and the exporter floor.
// Holding these SQL barriers is a scoped database predicate, not a host/runtime
// grant. File/Docker/maintenance exclusion must remain held by the coordinator.
const hostCDCWriterBarrier int64 = 18933879273374539

type HostColdSQLBinding struct {
	SourceRevision          string `json:"source_revision"`
	RequestSHA256           string `json:"request_sha256"`
	ContainmentIntentSHA256 string `json:"containment_intent_sha256"`
}

type hostColdSQLKey struct{}

type HostColdSQL struct {
	mu           sync.Mutex
	conn         *pgx.Conn
	pool         *pgxpool.Pool
	binding      HostColdSQLBinding
	body, digest string
	active       bool
}

func (s *HostColdSQL) Body() string   { s.mu.Lock(); defer s.mu.Unlock(); return s.body }
func (s *HostColdSQL) SHA256() string { s.mu.Lock(); defer s.mu.Unlock(); return s.digest }

var hostSQLBarriers = []int64{OrdinaryLeaseBarrier, routingEpochBarrier, hostCDCWriterBarrier}

// WithHostColdSQL retains exclusive session barriers across independently
// committed cold transactions. It destroys its private connection on every
// exit, so a failed unlock cannot return a locked session to the pool. Existing
// cold operations in the callback use this same backend, preserving the durable
// intent-before-nontransactional-effects contract. A returned receipt is only a
// past observation; the session scope ends when the callback returns.
func WithHostColdSQL(ctx context.Context, pool *pgxpool.Pool, binding HostColdSQLBinding, fn func(context.Context, *HostColdSQL) error) error {
	if ctx == nil || ctx.Err() != nil || pool == nil || fn == nil || !ownershipRevision.MatchString(binding.SourceRevision) || !ownershipSHA256.MatchString(binding.RequestSHA256) || !ownershipSHA256.MatchString(binding.ContainmentIntentSHA256) || ctx.Value(hostColdSQLKey{}) != nil {
		return ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	owned, err := pool.Acquire(ctx)
	if err != nil {
		return authorityError(err)
	}
	conn := owned.Hijack()
	s := &HostColdSQL{conn: conn, pool: pool, binding: binding, active: true}
	defer func() {
		s.mu.Lock()
		s.active = false
		s.mu.Unlock()
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = conn.Close(cleanup)
	}()
	if _, err := conn.Exec(ctx, "SET statement_timeout='10s'"); err != nil {
		return ErrObservation
	}
	// Never wait while holding a partial barrier set: legacy trigger ordering
	// can differ. Release the acquired prefix before retrying the whole set.
	for {
		acquired := []int64{}
		for _, key := range hostSQLBarriers {
			var locked bool
			if err := conn.QueryRow(ctx, "SELECT pg_catalog.pg_try_advisory_lock($1)", key).Scan(&locked); err != nil {
				return ErrObservation
			}
			if !locked {
				break
			}
			acquired = append(acquired, key)
		}
		if len(acquired) == len(hostSQLBarriers) {
			break
		}
		for n := len(acquired) - 1; n >= 0; n-- {
			var unlocked bool
			if conn.QueryRow(ctx, "SELECT pg_catalog.pg_advisory_unlock($1)", acquired[n]).Scan(&unlocked) != nil || !unlocked {
				return ErrObservation
			}
		}
		select {
		case <-ctx.Done():
			return authorityError(ctx.Err())
		case <-time.After(25 * time.Millisecond):
		}
	}
	if err := s.observe(ctx); err != nil {
		return err
	}
	scoped := context.WithValue(ctx, hostColdSQLKey{}, s)
	if err := fn(scoped, s); err != nil {
		return authorityError(err)
	}
	return s.Check(scoped)
}

// Check reobserves the exact backend's held locks and all live legacy SQL lease
// columns. It does not clear leases, infer expiry from a caller clock, or claim
// that arbitrary nonparticipating SQL clients are excluded.
func (s *HostColdSQL) Check(ctx context.Context) error {
	if ctx == nil || ctx.Err() != nil || s == nil || ctx.Value(hostColdSQLKey{}) != s {
		return ErrAuthorityLost
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.observe(ctx)
}

func (s *HostColdSQL) observe(ctx context.Context) error {
	if !s.active || s.conn == nil || s.conn.IsClosed() || s.conn.PgConn().TxStatus() != 'I' {
		return ErrAuthorityLost
	}
	var locks, boards, postings int64
	var pid int32
	var database string
	err := s.conn.QueryRow(ctx, `SELECT pg_backend_pid(),current_database(),
 (SELECT count(*) FROM pg_catalog.pg_locks WHERE locktype='advisory'
   AND pid=pg_backend_pid() AND mode='ExclusiveLock' AND granted AND objsubid=1
   AND ((classid::bigint<<32)|objid::bigint)=ANY($1::bigint[])),
 (SELECT count(*) FROM public.job_board WHERE leased_until>clock_timestamp()),
 (SELECT count(*) FROM public.job_posting WHERE leased_until>clock_timestamp())`, hostSQLBarriers).Scan(&pid, &database, &locks, &boards, &postings)
	if err != nil {
		return ErrObservation
	}
	if locks != int64(len(hostSQLBarriers)) || pid != int32(s.conn.PgConn().PID()) || boards != 0 || postings != 0 {
		return ErrAuthorityLost
	}
	h := sha256.Sum256([]byte(database))
	b, err := json.Marshal(struct {
		Version           string             `json:"version"`
		Binding           HostColdSQLBinding `json:"binding"`
		BackendPID        int32              `json:"backend_pid"`
		DatabaseSHA256    string             `json:"database_sha256"`
		ExclusiveBarriers []int64            `json:"exclusive_barriers"`
		LiveBoardLeases   int64              `json:"live_board_leases"`
		LivePostingLeases int64              `json:"live_posting_leases"`
		RuntimeAdmission  bool               `json:"runtime_admission"`
	}{"jobseek.crawler-host-cold-sql/v1", s.binding, pid, hex.EncodeToString(h[:]), hostSQLBarriers, boards, postings, false})
	if err != nil {
		return ErrObservation
	}
	if s.body != "" && s.body != string(b) {
		return ErrAuthorityLost
	}
	d := sha256.Sum256(b)
	s.body, s.digest = string(b), hex.EncodeToString(d[:])
	return nil
}

func hostColdSQLTransaction(ctx context.Context, pool *pgxpool.Pool, fn func(context.Context, pgx.Tx) error) (bool, error) {
	s, ok := ctx.Value(hostColdSQLKey{}).(*HostColdSQL)
	if !ok {
		return false, nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if pool != s.pool || s.observe(ctx) != nil {
		return true, ErrAuthorityLost
	}
	err := pgx.BeginFunc(ctx, s.conn, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SET LOCAL statement_timeout='10s'"); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "SET LOCAL idle_in_transaction_session_timeout='15s'"); err != nil {
			return err
		}
		return fn(ctx, tx)
	})
	if err != nil {
		return true, authorityError(err)
	}
	return true, s.observe(ctx)
}
