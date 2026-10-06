package queue

import (
	"bytes"
	"context"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"math"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
)

//go:embed lifecycle/*.sql
var greenhouseLifecycleSQL embed.FS

func lifecycleQuery(name string) string {
	body, err := greenhouseLifecycleSQL.ReadFile("lifecycle/" + name + ".sql")
	if err != nil {
		panic("missing compiled lifecycle statement")
	}
	return string(body)
}

// GreenhouseCycle binds discovery start and committed posting accounting to one
// installed claim. Network/CPU work remains outside the bounded SQL transaction.
// Its methods serialize chunk writes and terminal decisions for that attempt.
type GreenhouseCycle struct {
	mu           sync.Mutex
	authority    *Authority
	claim        *Claim
	startedAt    time.Time
	processed    int
	identities   map[string]bool
	failed, done bool
}

type GreenhouseInventorySummary struct {
	// Discovered counts distinct raw URLs before processor URL sanity filtering
	// and canonical identity collapse, matching the ordinary Python monitor.
	Discovered, ProcessingFiltered int
	Truncated                      bool
	MetadataUpdates                map[string]any
}

type GreenhouseCycleResult struct {
	Receipt             *Receipt
	Status, GoneSkipped string
	Gone                int
	RecoveredFrom       *string
	EnteredQuarantine   bool
	HostCircuit         *HostCircuitOutcome
}

// InvalidateInventory rejects later success/absence finalization after a
// fetch/normalization/preparation failure outside a posting transaction. It
// performs no database/queue mutation and leaves failure/reservation handling
// or lease recovery available, including after context cancellation.
func (c *GreenhouseCycle) InvalidateInventory() {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.done {
		c.failed = true
	}
}

func (a *Authority) BeginGreenhouseCycle(ctx context.Context, claim *Claim) (*GreenhouseCycle, error) {
	if a == nil || !a.valid(claim) || a.ownership == nil || claim.task.Kind != Monitor || claim.recovered != nil {
		return nil, ErrConfiguration
	}
	claim.cycleMu.Lock()
	defer claim.cycleMu.Unlock()
	if claim.cycleStarted {
		return nil, ErrConfiguration
	}
	cycle := &GreenhouseCycle{authority: a, claim: claim, identities: map[string]bool{}}
	_, err := a.Write(ctx, claim, false, func(ctx context.Context, tx pgx.Tx) error {
		var reserved bool
		if err := tx.QueryRow(ctx, "SELECT now(),tdm_reserved FROM public.job_board WHERE id=$1::uuid", claim.task.ID).Scan(&cycle.startedAt, &reserved); err != nil {
			return err
		}
		if reserved {
			return ErrPublisherReserved
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	claim.cycleStarted = true
	return cycle, nil
}

func (c *GreenhouseCycle) WriteRichBatch(ctx context.Context, batch []GreenhouseRichPosting) (*GreenhouseRichBatchResult, error) {
	if c == nil || c.authority == nil {
		return nil, ErrConfiguration
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.done || c.failed {
		return nil, ErrConfiguration
	}
	result, err := c.authority.WriteGreenhouseRichBatch(ctx, c.claim, batch)
	if err != nil {
		// An ambiguous/failed posting transaction must never permit absence
		// finalization. It can only enter failure/reservation handling or recovery.
		c.failed = true
		return nil, err
	}
	c.processed += len(batch)
	for _, posting := range batch {
		c.identities[posting.URL] = true
	}
	return result, nil
}

// FinishSuccess records guarded disappearance/empty/partial state and the
// canonical due time atomically with the terminal attempt receipt. Runtime
// lifecycle metadata is read freshly from PostgreSQL, never from the detached
// Redis claim snapshot. That snapshot stays unchanged through settlement.
func (c *GreenhouseCycle) FinishSuccess(ctx context.Context, inventory GreenhouseInventorySummary) (*GreenhouseCycleResult, error) {
	if c == nil || c.authority == nil {
		return nil, ErrConfiguration
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.done || c.failed || inventory.Discovered < c.processed || inventory.ProcessingFiltered < 0 || inventory.ProcessingFiltered > inventory.Discovered-c.processed {
		return nil, ErrConfiguration
	}
	result := &GreenhouseCycleResult{Status: "succeeded"}
	receipt, err := c.authority.Write(ctx, c.claim, true, func(ctx context.Context, tx pgx.Tx) error {
		md, err := c.metadata(ctx, tx, true)
		if err != nil {
			return err
		}
		if len(inventory.MetadataUpdates) > 0 {
			if inventory.Truncated || inventory.ProcessingFiltered != 0 || inventory.Discovered != c.processed || c.processed != len(c.identities) || c.claim.task.Config["crawler_type"] != "eightfold" {
				return ErrConfiguration
			}
			if err := validateEightfoldWatermarkUpdate(c.claim.task.Config, md, inventory.MetadataUpdates); err != nil {
				return err
			}
			if err := c.patch(ctx, tx, inventory.MetadataUpdates); err != nil {
				return err
			}
		}
		fingerprint, _ := md["_monitor_config_fingerprint"].(string)
		var expected *string
		if fingerprint != "" {
			expected = &fingerprint
		}
		if c.processed == 0 {
			if err := c.patch(ctx, tx, map[string]any{"_confirmed_drop_candidate": nil}); err != nil {
				return err
			}
			var status string
			var delist bool
			if err := tx.QueryRow(ctx, lifecycleQuery("empty"), c.claim.task.ID, expected).Scan(&status, &delist, &result.RecoveredFrom); err != nil {
				return err
			}
			if delist {
				result.Gone, err = c.countReturned(ctx, tx, "delist", c.claim.task.ID)
			}
			return err
		}
		if inventory.Truncated {
			result.GoneSkipped = "truncated"
			if err := c.patch(ctx, tx, map[string]any{"_confirmed_drop_candidate": nil}); err != nil {
				return err
			}
		} else {
			complete := inventory.ProcessingFiltered == 0 && inventory.Discovered == c.processed && c.processed == len(c.identities)
			result.Gone, result.GoneSkipped, err = c.markGone(ctx, tx, md, inventory.Discovered, complete)
			if err != nil {
				return err
			}
		}
		return tx.QueryRow(ctx, lifecycleQuery("success"), c.claim.task.ID, expected).Scan(&result.RecoveredFrom)
	})
	if err != nil {
		return nil, err
	}
	receipt.terminalOutcome = "succeeded"
	c.done, result.Receipt = true, receipt
	return result, nil
}

func (c *GreenhouseCycle) FinishFailure(ctx context.Context, message string) (*GreenhouseCycleResult, error) {
	return c.finishFailure(ctx, message, nil, GreenhouseHostObservation{})
}

// FinishFailureWithHostCircuit advances the shared circuit once for this actual
// board run, then commits its lower bound and learned host with canonical
// backoff. Protective Redis trouble still permits normal failure scheduling.
func (c *GreenhouseCycle) FinishFailureWithHostCircuit(ctx context.Context, message string, run *GreenhouseHostRun, observation GreenhouseHostObservation) (*GreenhouseCycleResult, error) {
	if c == nil || !run.valid(c) {
		return nil, ErrConfiguration
	}
	return c.finishFailure(ctx, message, run, observation)
}

func (c *GreenhouseCycle) finishFailure(ctx context.Context, message string, run *GreenhouseHostRun, observation GreenhouseHostObservation) (*GreenhouseCycleResult, error) {
	if c == nil || c.authority == nil || message == "" || len(message) > 4096 || !utf8.ValidString(message) || strings.ContainsRune(message, 0) {
		return nil, ErrConfiguration
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.done {
		return nil, ErrConfiguration
	}
	c.failed = true
	result := &GreenhouseCycleResult{Status: "failed"}
	var learned *string
	receipt, err := c.authority.write(ctx, c.claim, true, &learned, func(ctx context.Context, tx pgx.Tx) error {
		if _, err := c.metadata(ctx, tx, true); err != nil {
			return err
		}
		var enabled bool
		var success, quarantined *time.Time
		var status string
		if run != nil {
			var err error
			result.HostCircuit, err = run.failureOutcome(ctx, observation)
			if err != nil {
				return err
			}
			host := result.HostCircuit.Host
			learned = &host
		}
		if err := tx.QueryRow(ctx, lifecycleQuery("failure"), c.claim.task.ID, message).Scan(&enabled, &success, &status, &quarantined, &result.EnteredQuarantine); err != nil {
			return err
		}
		if result.HostCircuit != nil && result.HostCircuit.OpenUntil != nil {
			_, err := tx.Exec(ctx, "UPDATE public.job_board SET next_check_at=GREATEST(next_check_at,$2::timestamptz) WHERE id=$1::uuid", c.claim.task.ID, *result.HostCircuit.OpenUntil)
			return err
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	receipt.terminalOutcome = "failed"
	c.done, result.Receipt = true, receipt
	return result, nil
}

func (c *GreenhouseCycle) metadata(ctx context.Context, tx pgx.Tx, rejectReservation bool) (map[string]any, error) {
	var raw []byte
	var reserved bool
	if err := tx.QueryRow(ctx, "SELECT COALESCE(metadata,'{}'::jsonb),tdm_reserved FROM public.job_board WHERE id=$1::uuid", c.claim.task.ID).Scan(&raw, &reserved); err != nil {
		return nil, err
	}
	if reserved && rejectReservation {
		return nil, ErrPublisherReserved
	}
	var md map[string]any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if decoder.Decode(&md) != nil || md == nil {
		return nil, ErrConfiguration
	}
	return md, nil
}

func (c *GreenhouseCycle) patch(ctx context.Context, tx pgx.Tx, patch map[string]any) error {
	body, err := json.Marshal(patch)
	if err != nil {
		return ErrConfiguration
	}
	_, err = tx.Exec(ctx, lifecycleQuery("metadata"), c.claim.task.ID, string(body))
	return err
}

func (c *GreenhouseCycle) countReturned(ctx context.Context, tx pgx.Tx, query string, args ...any) (int, error) {
	rows, err := tx.Query(ctx, lifecycleQuery(query), args...)
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	count := 0
	for rows.Next() {
		count++
	}
	return count, rows.Err()
}

func lifecycleInteger(value any) (int64, error) {
	switch v := value.(type) {
	case nil:
		return 0, nil
	case bool:
		if v {
			return 1, nil
		}
		return 0, nil
	case json.Number:
		if n, err := v.Int64(); err == nil {
			return n, nil
		}
		n, err := v.Float64()
		if err != nil || math.IsNaN(n) || math.IsInf(n, 0) || n >= 1<<63 || n < -(1<<63) {
			return 0, ErrConfiguration
		}
		return int64(n), nil
	case string:
		if v == "" {
			return 0, nil
		}
		return strconv.ParseInt(strings.TrimSpace(v), 10, 64)
	}
	return 0, ErrConfiguration
}

func (c *GreenhouseCycle) markGone(ctx context.Context, tx pgx.Tx, md map[string]any, discovered int, complete bool) (int, string, error) {
	threshold, dropThreshold := 1, 0.3
	if kind := c.claim.task.Config["crawler_type"]; kind == "eightfold" || kind == "workday" || kind == "smartrecruiters" || kind == "workable" || kind == "join" || kind == "sitemap" || kind == "dom" || kind == "icims" || kind == "breezy" || kind == "jazzhr" || kind == "gupy" || kind == "phenom" || kind == "nextdata" || kind == "inline" {
		var err error
		threshold, err = workdayDelistThreshold(md["delist_threshold"])
		if err != nil {
			return 0, "", err
		}
		dropThreshold, err = workdayLifecycleSetting(md["drop_threshold"], 0.3)
		if err != nil {
			return 0, "", err
		}
	}
	blastFloor := 0.5
	if kind := c.claim.task.Config["crawler_type"]; kind == "eightfold" || kind == "workday" || kind == "smartrecruiters" || kind == "workable" || kind == "join" || kind == "sitemap" || kind == "dom" || kind == "icims" || kind == "breezy" || kind == "jazzhr" || kind == "gupy" || kind == "phenom" || kind == "nextdata" || kind == "inline" {
		var err error
		blastFloor, err = workdayLifecycleSetting(md["blast_radius_floor"], 0.5)
		if err != nil {
			return 0, "", err
		}
	} else if raw := md["blast_radius_floor"]; raw != nil {
		value, ok := raw.(json.Number)
		if !ok {
			return 0, "", ErrConfiguration
		}
		var err error
		blastFloor, err = value.Float64()
		if err != nil || math.IsNaN(blastFloor) || math.IsInf(blastFloor, 0) || blastFloor < 0 || blastFloor > 1 {
			return 0, "", ErrConfiguration
		}
	}
	history := []any{}
	if value := md["recent_discovered_counts"]; value != nil {
		var ok bool
		history, ok = value.([]any)
		if !ok {
			return 0, "", ErrConfiguration
		}
	}
	streak, err := lifecycleInteger(md["suspect_streak"])
	if err != nil || streak < 0 || streak >= 2147483647 {
		return 0, "", ErrConfiguration
	}
	values := make([]float64, len(history))
	for i, value := range history {
		n, ok := value.(json.Number)
		if !ok {
			return 0, "", ErrConfiguration
		}
		values[i], err = n.Float64()
		if err != nil || math.IsNaN(values[i]) || math.IsInf(values[i], 0) {
			return 0, "", ErrConfiguration
		}
	}
	skip := ""
	if len(values) >= 3 {
		sort.Float64s(values)
		median := values[len(values)/2]
		if len(values)%2 == 0 {
			median = (values[len(values)/2-1] + median) / 2
		}
		if median > 0 && float64(discovered) < median*(1-dropThreshold) {
			skip = "drop"
		}
	}
	config, _ := md["_monitor_config_fingerprint"].(string)
	confirmable := complete && config != ""
	active, missing := 0, 0
	if skip == "" || confirmable {
		if err := tx.QueryRow(ctx, lifecycleQuery("count"), c.claim.task.ID, c.startedAt).Scan(&active, &missing); err != nil {
			return 0, "", err
		}
		if skip == "" && active > 0 && float64(missing)/float64(active) > blastFloor {
			skip = "blast_radius"
		}
	}
	if skip != "" {
		patch := map[string]any{"suspect_streak": streak + 1}
		candidate := md["_confirmed_drop_candidate"]
		if confirmable {
			identities := make([]string, 0, len(c.identities))
			for identity := range c.identities {
				identities = append(identities, identity)
			}
			sort.Strings(identities)
			hash := sha256.New()
			for _, identity := range identities {
				hash.Write([]byte(identity))
				hash.Write([]byte{0})
			}
			inventory := hex.EncodeToString(hash.Sum(nil))
			confirmations := int64(1)
			if prior, ok := candidate.(map[string]any); ok && prior["inventory_fingerprint"] == inventory && prior["config_fingerprint"] == config {
				// Python compares the stored discovered value directly with an
				// integer; a numeric string must not confirm the same inventory.
				priorNumber, numeric := prior["discovered"].(json.Number)
				priorDiscovered, err := priorNumber.Float64()
				if numeric && err == nil && priorDiscovered == float64(discovered) {
					old, err := lifecycleInteger(prior["confirmations"])
					if err != nil {
						old = 0
					}
					if old == math.MaxInt64 {
						return 0, "", ErrConfiguration
					}
					confirmations = old + 1
				}
			}
			patch["_confirmed_drop_candidate"] = map[string]any{"inventory_fingerprint": inventory, "config_fingerprint": config, "discovered": discovered, "confirmations": confirmations}
			if confirmations >= 3 && missing <= 5000 {
				gone, err := c.countReturned(ctx, tx, "missing", c.claim.task.ID, c.startedAt, threshold)
				if err != nil {
					return 0, "", err
				}
				err = c.patch(ctx, tx, map[string]any{"recent_discovered_counts": []int{discovered}, "suspect_streak": 0, "_confirmed_drop_candidate": nil})
				return gone, "", err
			}
		} else if candidate != nil {
			patch["_confirmed_drop_candidate"] = nil
		}
		return 0, skip, c.patch(ctx, tx, patch)
	}
	gone, err := c.countReturned(ctx, tx, "missing", c.claim.task.ID, c.startedAt, threshold)
	if err != nil {
		return 0, "", err
	}
	history = append(history, discovered)
	if len(history) > 5 {
		history = history[len(history)-5:]
	}
	err = c.patch(ctx, tx, map[string]any{"recent_discovered_counts": history, "suspect_streak": 0, "_confirmed_drop_candidate": nil})
	return gone, "", err
}
