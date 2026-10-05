package queue

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"regexp"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// OrdinaryLeaseBarrier orders native database commits before lease-ending
// transitions. Reapers/settlements take it exclusively, writers/claims share it.
// A Redis read alone cannot provide this cross-system ordering.
const OrdinaryLeaseBarrier int64 = 7544422533504811010

// Reuse the existing routing high-water and retirement lock; allocate no new
// epoch/control plane. Native ordinary selection must participate in the full
// quiesced cutover before relying on this global epoch.
const routingEpochBarrier int64 = 7544422533504811009

//go:embed authority.sql
var authoritySchema string

var ErrAuthorityLost = errors.New("ordinary worker authority lost")

type callbackFailure struct{ cause error }

func (e *callbackFailure) Error() string { return "ordinary writer callback failed" }
func (e *callbackFailure) Unwrap() error { return e.cause }
func authorityError(err error) error {
	if err == nil {
		return nil
	}
	var callback *callbackFailure
	if errors.As(err, &callback) {
		return callback
	}
	for _, known := range []error{ErrAuthorityLost, ErrConfiguration, ErrObservation, ErrUnsupportedProfile, context.Canceled, context.DeadlineExceeded} {
		if errors.Is(err, known) {
			return known
		}
	}
	return ErrObservation
}

var canonicalUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// WithOrdinaryLeaseRetirement must surround every native lease-ending Redis
// sweep/settlement, holding the database barrier through its acknowledgement.
func WithOrdinaryLeaseRetirement(ctx context.Context, pool *pgxpool.Pool, fn func(context.Context) error) error {
	if fn == nil {
		return ErrConfiguration
	}
	return withOrdinaryLeaseRetirementTx(ctx, pool, func(ctx context.Context, _ pgx.Tx) error { return fn(ctx) })
}

func withOrdinaryLeaseRetirementTx(ctx context.Context, pool *pgxpool.Pool, fn func(context.Context, pgx.Tx) error) error {
	if pool == nil || fn == nil {
		return ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return pgx.BeginFunc(ctx, pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", OrdinaryLeaseBarrier); err != nil {
			return err
		}
		return fn(ctx, tx)
	})
}

type Authority struct {
	queue               *Client
	pool                *pgxpool.Pool
	epoch               int64
	ownership           *OwnershipPlan
	ownershipCursor     int
	detailCursors       map[WorkerType]int
	detailPostingCursor map[string]string
	ownershipMu         sync.Mutex
}

// Claim is an immutable identity captured from a tokenized queue claim. A
// partial claim/error pair still retains the inflight attempt for recovery.
type Claim struct {
	owner        *Authority
	task         Task
	boardID      string
	configDigest string
	recovered    *Receipt
	cycleMu      sync.Mutex
	cycleStarted bool
	hostRun      *GreenhouseHostRun
}

// Descriptor returns a detached configuration snapshot, without claim tokens.
func (claim *Claim) Descriptor() Task {
	if claim == nil {
		return Task{}
	}
	task := claim.task
	task.claimToken = ""
	task.Config = cloneConfig(task.Config)
	return task
}

// OwnershipBound reports how this opaque claim was installed, not current
// permission. Writes/settlement still revalidate the exact plan and epoch.
func (claim *Claim) OwnershipBound() bool {
	return claim != nil && claim.owner != nil && claim.owner.ownership != nil
}
func cloneConfig(config map[string]string) map[string]string {
	copy := make(map[string]string, len(config))
	for key, value := range config {
		copy[key] = value
	}
	return copy
}
func configDigest(config map[string]string) string {
	body, _ := json.Marshal(config)
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}

// Receipt binds the committed canonical deadline to one attempt. Nil deadline
// means a deliberately unscheduled detail. Callers cannot supply an arbitrary
// due time to native settlement.
type Receipt struct {
	claim           *Claim
	nextDue         *time.Time
	learnedHost     *string
	terminalOutcome string
}

func (r *Receipt) NextDue() *time.Time {
	if r == nil || r.nextDue == nil {
		return nil
	}
	copy := *r.nextDue
	return &copy
}
func (claim *Claim) RecoveredReceipt() *Receipt {
	if claim == nil {
		return nil
	}
	return claim.recovered
}

func OpenAuthority(ctx context.Context, dsn string, client *Client, epoch int64) (*Authority, error) {
	if client == nil || epoch < 1 || epoch > 9999999999999 {
		return nil, ErrConfiguration
	}
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, ErrConfiguration
	}
	// The ordinary runtime has five monitor slots. A blocked canonical row or
	// large posting chunk must not consume unrelated slots' transaction budget
	// while they wait for one connection. Keep a fixed, bounded pool; each
	// transaction still independently holds the same epoch/lease/row fences.
	config.MinConns, config.MaxConns = 0, 5
	config.MaxConnIdleTime = time.Minute
	config.ConnConfig.ConnectTimeout = 3 * time.Second
	config.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeDescribeExec
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:ordinary-authority:local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "10s"
	config.ConnConfig.RuntimeParams["idle_in_transaction_session_timeout"] = "15s"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, ErrObservation
	}
	authority := &Authority{queue: client, pool: pool, epoch: epoch}
	if err := authority.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		rows, err := tx.Query(ctx, "SELECT task_kind,task_id,board_id,routing_epoch,claim_token,config_sha256,state,next_due_at,learned_egress_host FROM public.ordinary_worker_write_fence WHERE false")
		if err != nil {
			return err
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}
		rows, err = tx.Query(ctx, "SELECT plan_sha256,routing_epoch,source_revision,payload,state,created_at FROM public.ordinary_worker_ownership_plan WHERE false")
		if err != nil {
			return err
		}
		rows.Close()
		return rows.Err()
	}); err != nil {
		pool.Close()
		return nil, authorityError(err)
	}
	return authority, nil
}
func (a *Authority) Close() { a.pool.Close() }

func (a *Authority) transaction(ctx context.Context, retire bool, fn func(context.Context, pgx.Tx) error) error {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	err := pgx.BeginFunc(ctx, a.pool, func(tx pgx.Tx) error {
		lock := "SELECT pg_advisory_xact_lock_shared($1)"
		if retire {
			lock = "SELECT pg_advisory_xact_lock($1)"
		}
		if _, err := tx.Exec(ctx, lock, OrdinaryLeaseBarrier); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock_shared($1)", routingEpochBarrier); err != nil {
			return err
		}
		var epoch int64
		var called bool
		if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq").Scan(&epoch, &called); err != nil {
			return err
		}
		if !called || epoch != a.epoch {
			return ErrAuthorityLost
		}
		if err := fn(ctx, tx); err != nil {
			return err
		}
		return ctx.Err()
	})
	return authorityError(err)
}
func (a *Authority) valid(claim *Claim) bool {
	return claim != nil && claim.owner == a && claim.task.Fenced()
}
func (a *Authority) current(ctx context.Context, claim *Claim) error {
	if !a.valid(claim) {
		return ErrConfiguration
	}
	if a.ownership != nil {
		if err := a.queue.verifyOwnershipProjection(ctx, a.ownership); err != nil {
			return err
		}
	}
	prefix := "scrape:"
	if claim.task.Kind == Monitor {
		prefix = "board:"
	}
	config, err := a.queue.redis.HGetAll(ctx, prefix+claim.task.ID).Result()
	if err != nil {
		return ErrObservation
	}
	if claim.configDigest != "" && configDigest(config) != claim.configDigest {
		return ErrAuthorityLost
	}
	alive, err := a.queue.Heartbeat(ctx, &claim.task)
	if err != nil {
		return err
	}
	if !alive {
		return ErrAuthorityLost
	}
	return nil
}

func canonicalDue(ctx context.Context, tx pgx.Tx, claim *Claim) (*time.Time, error) {
	var due *time.Time
	query := "SELECT next_check_at FROM public.job_board WHERE id=$1::uuid"
	if claim.task.Kind == Scrape {
		query = "SELECT next_scrape_at FROM public.job_posting WHERE id=$1::uuid AND board_id=$2::uuid"
	}
	args := []any{claim.task.ID}
	if claim.task.Kind == Scrape {
		args = append(args, claim.boardID)
	}
	if err := tx.QueryRow(ctx, query, args...).Scan(&due); err != nil {
		return nil, err
	}
	return due, nil
}

// Claim activates only while holding the database/lease barriers and after a
// fresh atomic Redis validation. A delayed expired/reaped/retired attempt never
// activates merely because it reaches PostgreSQL later.
func (a *Authority) Claim(ctx context.Context, worker WorkerType) (*Claim, error) {
	var claim *Claim
	err := a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		if err := a.requireOwnership(ctx, tx, nil); err != nil {
			return err
		}
		var task *Task
		var err error
		if a.ownership == nil {
			task, err = a.queue.ClaimFenced(ctx, worker)
		} else {
			task, err = a.claimOwned(ctx, tx, worker)
		}
		if task != nil {
			claim = &Claim{owner: a, task: *task}
			claim.task.Config = cloneConfig(task.Config)
		}
		if err != nil || task == nil {
			return err
		}
		claim.boardID = task.Config["board_id"]
		if task.Kind == Monitor {
			claim.boardID = task.ID
		}
		if !canonicalUUID.MatchString(task.ID) || !canonicalUUID.MatchString(claim.boardID) {
			return ErrConfiguration
		}
		claim.configDigest = configDigest(task.Config)
		if err := a.current(ctx, claim); err != nil {
			return err
		}
		// Keep board deletion/configuration updates outside this transaction; detail
		// identity is separately checked against its authoritative board mapping.
		var board string
		if err := tx.QueryRow(ctx, "SELECT id::text FROM public.job_board WHERE id=$1::uuid FOR UPDATE", claim.boardID).Scan(&board); err != nil {
			return err
		}
		if task.Kind == Scrape {
			var postingBoard string
			if err := tx.QueryRow(ctx, "SELECT board_id::text FROM public.job_posting WHERE id=$1::uuid AND board_id=$2::uuid FOR UPDATE", task.ID, claim.boardID).Scan(&postingBoard); err != nil {
				return err
			}
		}
		due, err := canonicalDue(ctx, tx, claim)
		if err != nil {
			return err
		}
		var state, oldToken, oldDigest, oldBoard string
		var oldEpoch int64
		var oldDue *time.Time
		var oldHost *string
		err = tx.QueryRow(ctx, `SELECT state,claim_token,config_sha256,board_id::text,routing_epoch,next_due_at,learned_egress_host
   FROM public.ordinary_worker_write_fence WHERE task_kind=$1 AND task_id=$2::uuid FOR UPDATE`, string(task.Kind), task.ID).Scan(&state, &oldToken, &oldDigest, &oldBoard, &oldEpoch, &oldDue, &oldHost)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		var now time.Time
		if err := tx.QueryRow(ctx, "SELECT clock_timestamp()").Scan(&now); err != nil {
			return err
		}
		recovered := state == "completed" && oldEpoch <= a.epoch && oldDigest == claim.configDigest && oldBoard == claim.boardID && ((oldDue == nil && due == nil) || (oldDue != nil && due != nil && oldDue.Equal(*due) && due.After(now)))
		nextState := "active"
		var retainedDue *time.Time
		var retainedHost *string
		if recovered {
			nextState = "completed"
			retainedDue = due
			retainedHost = oldHost
		}
		_, err = tx.Exec(ctx, `INSERT INTO public.ordinary_worker_write_fence
   (task_kind,task_id,board_id,routing_epoch,claim_token,config_sha256,state,next_due_at,learned_egress_host)
   VALUES ($1,$2::uuid,$3::uuid,$4,$5,$6,$7,$8,$9)
   ON CONFLICT(task_kind,task_id) DO UPDATE SET board_id=EXCLUDED.board_id,
   routing_epoch=EXCLUDED.routing_epoch,claim_token=EXCLUDED.claim_token,
   config_sha256=EXCLUDED.config_sha256,state=EXCLUDED.state,next_due_at=EXCLUDED.next_due_at,learned_egress_host=EXCLUDED.learned_egress_host,updated_at=clock_timestamp()`, string(task.Kind), task.ID, claim.boardID, a.epoch, task.claimToken, claim.configDigest, nextState, retainedDue, retainedHost)
		if err != nil {
			return err
		}
		if recovered {
			claim.recovered = &Receipt{claim: claim, nextDue: due, learnedHost: retainedHost}
		}
		return a.current(ctx, claim)
	})
	if err != nil && claim != nil {
		claim.recovered = nil
	}
	return claim, err
}

func (a *Authority) require(ctx context.Context, tx pgx.Tx, claim *Claim, state string) (*time.Time, error) {
	var due *time.Time
	var board string
	if err := tx.QueryRow(ctx, "SELECT id::text FROM public.job_board WHERE id=$1::uuid FOR UPDATE", claim.boardID).Scan(&board); err != nil {
		return nil, err
	}
	if claim.task.Kind == Scrape {
		var postingBoard string
		if err := tx.QueryRow(ctx, "SELECT board_id::text FROM public.job_posting WHERE id=$1::uuid AND board_id=$2::uuid FOR UPDATE", claim.task.ID, claim.boardID).Scan(&postingBoard); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return nil, ErrAuthorityLost
			}
			return nil, err
		}
	}
	err := tx.QueryRow(ctx, `SELECT next_due_at FROM public.ordinary_worker_write_fence
  WHERE task_kind=$1 AND task_id=$2::uuid AND board_id=$3::uuid AND routing_epoch=$4
  AND claim_token=$5 AND config_sha256=$6 AND state=$7 FOR UPDATE`, string(claim.task.Kind), claim.task.ID, claim.boardID, a.epoch, claim.task.claimToken, claim.configDigest, state).Scan(&due)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	return due, err
}

// Write encloses all canonical effects in the same attempt/epoch transaction.
// Fetch/render/enrichment runs before entering this bounded transaction. A
// terminal write records the database-owned due time for settlement/recovery.
func (a *Authority) Write(ctx context.Context, claim *Claim, terminal bool, fn func(context.Context, pgx.Tx) error) (*Receipt, error) {
	return a.write(ctx, claim, terminal, nil, fn)
}

// learned is private terminal evidence, frozen with the due time after callback.
func (a *Authority) write(ctx context.Context, claim *Claim, terminal bool, learned **string, fn func(context.Context, pgx.Tx) error) (*Receipt, error) {
	if !a.valid(claim) || claim.configDigest == "" || fn == nil {
		return nil, ErrConfiguration
	}
	var receipt *Receipt
	err := a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		if err := a.requireOwnership(ctx, tx, claim); err != nil {
			return err
		}
		if err := a.current(ctx, claim); err != nil {
			return err
		}
		if _, err := a.require(ctx, tx, claim, "active"); err != nil {
			return err
		}
		if err := fn(ctx, tx); err != nil {
			return &callbackFailure{cause: err}
		}
		if terminal {
			due, err := canonicalDue(ctx, tx, claim)
			if err != nil {
				return err
			}
			var host *string
			if learned != nil && *learned != nil {
				value := **learned
				host = &value
			}
			_, err = tx.Exec(ctx, `UPDATE public.ordinary_worker_write_fence SET state='completed',next_due_at=$3,learned_egress_host=$4,updated_at=clock_timestamp()
    WHERE task_kind=$1 AND task_id=$2::uuid`, string(claim.task.Kind), claim.task.ID, due, host)
			if err != nil {
				return err
			}
			receipt = &Receipt{claim: claim, nextDue: due, learnedHost: host}
		}
		return a.current(ctx, claim)
	})
	if err != nil {
		return nil, err
	}
	return receipt, nil
}

// Settle serializes lease removal with every native database writer. It accepts
// only a committed/recovered receipt and rechecks the canonical deadline.
func (a *Authority) Settle(ctx context.Context, claim *Claim, receipt *Receipt) error {
	if !a.valid(claim) || receipt == nil || receipt.claim != claim {
		return ErrConfiguration
	}
	return a.transaction(ctx, true, func(ctx context.Context, tx pgx.Tx) error {
		if err := a.requireOwnership(ctx, tx, claim); err != nil {
			return err
		}
		if err := a.current(ctx, claim); err != nil {
			return err
		}
		retained, err := a.require(ctx, tx, claim, "completed")
		if err != nil {
			return err
		}
		canonical, err := canonicalDue(ctx, tx, claim)
		if err != nil {
			return err
		}
		equal := func(x, y *time.Time) bool { return x == nil && y == nil || x != nil && y != nil && x.Equal(*y) }
		if !equal(retained, receipt.nextDue) || !equal(canonical, receipt.nextDue) {
			return ErrAuthorityLost
		}
		var retainedHost *string
		if err := tx.QueryRow(ctx, "SELECT learned_egress_host FROM public.ordinary_worker_write_fence WHERE task_kind=$1 AND task_id=$2::uuid", string(claim.task.Kind), claim.task.ID).Scan(&retainedHost); err != nil {
			return err
		}
		if (retainedHost == nil) != (receipt.learnedHost == nil) || retainedHost != nil && *retainedHost != *receipt.learnedHost {
			return ErrAuthorityLost
		}
		var accepted bool
		if receipt.nextDue == nil {
			accepted, err = a.queue.Complete(ctx, &claim.task)
		} else {
			accepted, err = a.queue.rescheduleHost(ctx, &claim.task, seconds(*receipt.nextDue), receipt.learnedHost)
		}
		if err != nil {
			return err
		}
		if !accepted {
			return ErrAuthorityLost
		}
		return nil
	})
}

func (a *Authority) Heartbeat(ctx context.Context, claim *Claim) error {
	if !a.valid(claim) {
		return ErrConfiguration
	}
	return a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		if err := a.requireOwnership(ctx, tx, claim); err != nil {
			return err
		}
		return a.current(ctx, claim)
	})
}
