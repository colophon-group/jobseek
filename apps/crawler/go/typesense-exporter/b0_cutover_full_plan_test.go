package main

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func b0PlanPostgresFixture(t *testing.T, ctx context.Context) (*pgxpool.Pool, string) {
	t.Helper()
	dsn := os.Getenv("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("private canonical fixture required")
		}
		t.Skip("requires private canonical fixture")
	}
	u, err := url.Parse(dsn)
	if err != nil || !(u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1") || !strings.HasSuffix(u.Path, "_ordinary_worker_test") {
		t.Fatal("requires private canonical fixture")
	}
	observer, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal("private observer unavailable")
	}
	t.Cleanup(observer.Close)
	schema := fmt.Sprintf("b0_full_plan_%d", time.Now().UnixNano())
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err = observer.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal("private schema unavailable")
	}
	t.Cleanup(func() { _, _ = observer.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	ddl := `CREATE TABLE ` + quoted + `.job_board(id uuid,board_slug text,board_url text,is_enabled boolean,board_status text,metadata jsonb,crawler_type text,scraper_needs_browser boolean,scrape_interval_hours integer);
 CREATE TABLE ` + quoted + `.job_posting(id uuid,board_id uuid,source_url text,description_r2_hash bigint,is_active boolean,next_scrape_at timestamptz,leased_until timestamptz);
 CREATE TABLE ` + quoted + `.lightpanda_b0_write_fence(job_posting_id uuid,engine_owner text,shard_id text,routing_epoch bigint);`
	if _, err = observer.Exec(ctx, ddl); err != nil {
		t.Fatal("private relations unavailable")
	}
	params := u.Query()
	params.Set("search_path", schema)
	u.RawQuery = params.Encode()
	config, err := providerCutoverPoolConfig(u.String(), "lightpanda-b0-activation")
	if err != nil {
		t.Fatal("private pool settings failed")
	}
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal("private canonical pool unavailable")
	}
	t.Cleanup(pool.Close)
	return pool, u.String()
}

type b0FullPlanVector struct {
	Name, Operation string
	SourceReceipt   string `json:"source_receipt"`
	MarkerPhase     string `json:"marker_phase"`
	Board, Posting  map[string]any
	Seed, After     map[string]struct {
		Type  string `json:"type"`
		Value any    `json:"value"`
	}
	LuaCalls []struct {
		SHA      string
		KeyCount int `json:"key_count"`
		Args     []any
	} `json:"lua_calls"`
	Requests  []map[string]any
	Replies   []capacityProducerDecision
	Payloads  []string
	Applied   map[string]any
	Occupancy int64
	Accepted  bool
	Document  map[string]any
	Digest    string
}

type b0CapturedProducer struct {
	t      *testing.T
	vector b0FullPlanVector
	next   int
	q      *b0ColdQueue
	ctx    context.Context
}

func (p *b0CapturedProducer) cutoverManifest(_ context.Context, cohort string) (capacityProducerDecision, error) {
	count, err := p.q.client.HLen(p.ctx, p.q.keys[1]).Result()
	if err != nil {
		return capacityProducerDecision{}, err
	}
	return capacityProducerDecision{Version: capacityProducerProtocol, Outcome: "manifest", Reason: "manifest", Cohort: cohort, BoardSlugs: []string{p.vector.Board["board_slug"].(string)}, LifetimeOccupancy: count, LifetimeCapacity: 2048, LifetimeHeadroom: 2048 - count}, nil
}
func (p *b0CapturedProducer) cutoverTask(_ context.Context, operation, domain, posting string, score float64, config map[string]string, first bool, source, digest string) (capacityProducerDecision, error) {
	if p.next >= len(p.vector.Requests) {
		p.t.Fatal("unexpected producer request")
	}
	request := map[string]any{"operation": operation, "domain": domain, "posting_id": posting, "next_scrape_at": score, "config": config, "browser": true, "operator_transfer": true, "first_time": first, "legacy_schedule_score": source, "expected_digest": digest}
	got, err := b0task.CanonicalJSON(request, true)
	if err != nil {
		p.t.Fatal(err)
	}
	expected := map[string]any{}
	for k, v := range p.vector.Requests[p.next] {
		expected[k] = v
	}
	originalScore, err := strconv.ParseFloat(string(expected["next_scrape_at"].(json.Number)), 64)
	if err != nil {
		p.t.Fatal(err)
	}
	expected["next_scrape_at"] = originalScore
	want, err := b0task.CanonicalJSON(expected, true)
	if err != nil {
		p.t.Fatal(err)
	}
	if string(got) != string(want) {
		p.t.Fatal("complete retained producer request differs", string(got), string(want))
	}
	reply := p.vector.Replies[p.next]
	if operation == "activate" {
		schema, err := p.q.ownerSchema(p.ctx)
		if err != nil {
			return capacityProducerDecision{}, err
		}
		if schema == "" {
			b0ColdPrivateCall(p.t, p.ctx, p.q, "initialize_producer", nil)
		}
		task, err := b0task.Decode(p.vector.Payloads[p.next], reply.PayloadSHA256, b0task.Route{ShardID: p.q.identity.Shard, RoutingEpoch: p.q.identity.Epoch, EngineOwner: "go"})
		if err != nil {
			p.t.Fatal(err)
		}
		bound := map[string]string{}
		for k, v := range config {
			bound[k] = v
		}
		if source != "" {
			bound["__operator_source_score"] = source
		}
		body, err := b0task.CanonicalJSON(bound, true)
		if err != nil {
			p.t.Fatal(err)
		}
		ft := "0"
		if first {
			ft = "1"
		}
		b0ColdPrivateCall(p.t, p.ctx, p.q, "activate_legacy", map[int]any{4: posting, 5: strconv.FormatInt(task.Envelope.ConfigRevision, 10), 8: strconv.FormatInt(task.Envelope.InitialReadyAtMS, 10), 10: task.Payload, 11: task.PayloadSHA256, 12: task.PayloadSHA1, 15: reply.ExistingPayloadSHA256, 18: string(body), 19: "1", 22: ft})
	}
	p.next++
	reply.Version = capacityProducerProtocol
	reply.Reason = "prepared"
	if operation == "activate" {
		reply.Reason = "already_activated"
		if reply.Activated {
			reply.Reason = "activated"
		}
	}
	return reply, nil
}

func b0SeedFullPlan(t *testing.T, ctx context.Context, pool *pgxpool.Pool, client *redis.Client, v b0FullPlanVector) {
	t.Helper()
	b0SeedFullPlanRedis(t, ctx, client, v)
	b := v.Board
	p := v.Posting
	metadata, err := json.Marshal(b["metadata"])
	if err != nil {
		t.Fatal(err)
	}
	if _, err = pool.Exec(ctx, `INSERT INTO job_board VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`, b["board_id"], b["board_slug"], b["board_url"], b["is_enabled"], b["board_status"], string(metadata), b["crawler_type"], b["scraper_needs_browser"], func() int64 {
		n, _ := strconv.ParseInt(string(b["scrape_interval_hours"].(json.Number)), 10, 64)
		return n
	}()); err != nil {
		t.Fatal("canonical board seed failed", err)
	}
	b0SeedFullPlanPosting(t, ctx, pool, p)
}

func b0SeedFullPlanRedis(t *testing.T, ctx context.Context, client *redis.Client, v b0FullPlanVector) {
	t.Helper()
	for key, entry := range v.Seed {
		switch entry.Type {
		case "string":
			if client.Set(ctx, key, entry.Value.(string), 0).Err() != nil {
				t.Fatal("private string seed failed")
			}
		case "hash":
			if client.HSet(ctx, key, entry.Value.(map[string]any)).Err() != nil {
				t.Fatal("private hash seed failed")
			}
		case "set":
			if client.SAdd(ctx, key, entry.Value.([]any)...).Err() != nil {
				t.Fatal("private set seed failed")
			}
		case "zset":
			for _, row := range entry.Value.([]any) {
				parts := row.([]any)
				score, err := strconv.ParseFloat(string(parts[1].(json.Number)), 64)
				if err != nil || client.ZAdd(ctx, key, redis.Z{Score: score, Member: parts[0]}).Err() != nil {
					t.Fatal("private zset seed failed")
				}
			}
		default:
			t.Fatal("unknown original seed type")
		}
	}
}

func b0SeedFullPlanPosting(t *testing.T, ctx context.Context, pool *pgxpool.Pool, p map[string]any) {
	t.Helper()
	next, err := time.Parse(time.RFC3339Nano, p["next_scrape_at"].(string))
	if err != nil {
		t.Fatal(err)
	}
	var hash any
	if p["description_r2_hash"] != nil {
		n, err := strconv.ParseInt(string(p["description_r2_hash"].(json.Number)), 10, 64)
		if err != nil {
			t.Fatal(err)
		}
		hash = n
	}
	var leased any
	if p["lease_active"] == true {
		leased = time.Now().Add(time.Hour)
	}
	if _, err = pool.Exec(ctx, `INSERT INTO job_posting VALUES($1,$2,$3,$4,$5,$6,$7)`, p["posting_id"], p["board_id"], p["source_url"], hash, p["is_active"], next, leased); err != nil {
		t.Fatal("canonical posting seed failed")
	}
	if _, err = pool.Exec(ctx, `INSERT INTO lightpanda_b0_write_fence VALUES($1,'go','lightpanda-b0',7)`, p["posting_id"]); err != nil {
		t.Fatal("canonical fence seed failed")
	}
}

func TestRealB0FullPlansMatchActualRetainedPlansAndDigests(t *testing.T) {
	body, err := os.ReadFile("testdata/python_b0_cutover_full_plan.json")
	if err != nil {
		t.Fatal(err)
	}
	// The original Python reply field names are snake_case. Decode through raw
	// maps once so typed corpus fields keep the actual retained wire names.
	var raw struct{ Vectors []json.RawMessage }
	if json.Unmarshal(body, &raw) != nil || len(raw.Vectors) != 36 {
		t.Fatal("full retained corpus unavailable", len(raw.Vectors))
	}
	for _, data := range raw.Vectors {
		var v b0FullPlanVector
		decoder := json.NewDecoder(strings.NewReader(string(data)))
		decoder.UseNumber()
		if decoder.Decode(&v) != nil {
			t.Fatal("full corpus malformed")
		}

		t.Run(v.Name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			pool, _ := b0PlanPostgresFixture(t, ctx)
			client := capacityRedisFixture(t, ctx)
			b0SeedFullPlan(t, ctx, pool, client, v)
			i := b0CutoverIdentity{Namespace: "production-b0", Shard: "lightpanda-b0", Epoch: 7, Cohort: "c1", SourceReceipt: v.SourceReceipt}
			q, err := newB0PlanQueue(client, i)
			if err != nil {
				t.Fatal(err)
			}
			directory := t.TempDir()
			if os.Chmod(directory, 0700) != nil {
				t.Fatal("private marker directory unavailable")
			}
			if v.MarkerPhase == "active" {
				doc := map[string]any{"schema": "jobseek.lightpanda.producer-activation/v1", "namespace": i.Namespace, "shard_id": i.Shard, "routing_epoch": i.Epoch, "engine_owner": "go", "cohort": i.Cohort, "board_slugs": []string{"browser-use-careers"}}
				payload, err := b0task.CanonicalJSON(doc, true)
				if err != nil || os.WriteFile(filepath.Join(directory, ".activation-v1"), append(append([]byte("A\n"), payload...), '\n'), 0600) != nil {
					t.Fatal("private active marker unavailable")
				}
			}
			producer := &b0CapturedProducer{t: t, vector: v, q: q, ctx: ctx}
			a := b0CutoverAdmin{pool: pool, queue: q, producer: producer, mode: "off", markerDirectory: directory, markerUID: uint32(os.Getuid())}
			var plan b0CutoverPlan
			before, err := capacityKeySnapshot(ctx, client)
			if err != nil {
				t.Fatal(err)
			}
			if v.Operation == "activate" {
				a.mode = "enabled"
				plan, err = a.activationPlan(ctx)
			} else {
				state := "active"
				if v.MarkerPhase == "absent" {
					state = "pending"
				}
				plan, err = a.rollbackPlan(ctx, state)
			}
			if (err == nil) != v.Accepted {
				t.Fatal("original complete planning acceptance differs", err)
			}
			if err == nil {
				got, _, err := b0PlanCanonical(plan.Document)
				if err != nil {
					t.Fatal(err)
				}
				want, err := b0task.CanonicalJSON(v.Document, true)
				if err != nil {
					t.Fatal(err)
				}
				if string(got) != string(want) || plan.Digest != v.Digest {
					t.Fatal("original full plan bytes/digest differ", string(got), string(want), plan.Digest, v.Digest)
				}
			}
			after, snapshotErr := capacityKeySnapshot(ctx, client)
			if snapshotErr != nil {
				t.Fatal(snapshotErr)
			}
			if !reflect.DeepEqual(before, after) {
				t.Fatal("read-only planning changed Redis authority")
			}

			if v.Accepted {
				plannedCalls := producer.next
				var applied map[string]any
				if v.Operation == "activate" {
					_, mismatchErr := a.applyActivation(ctx, strings.Repeat("f", 64))
					if mismatchErr == nil {
						t.Fatal("stale activation digest accepted")
					}
					afterMismatch, observeErr := capacityKeySnapshot(ctx, client)
					if observeErr != nil || !reflect.DeepEqual(before, afterMismatch) {
						t.Fatal("stale activation digest changed queue authority")
					}
					var fenceCount int64
					if pool.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence").Scan(&fenceCount) != nil || fenceCount != 1 {
						t.Fatal("stale activation digest changed canonical fences")
					}
					producer.next = plannedCalls
					applied, err = a.applyActivation(ctx, plan.Digest)
				} else {
					state := "active"
					if v.MarkerPhase == "absent" {
						state = "pending"
					}
					_, mismatchErr := a.applyRollback(ctx, state, strings.Repeat("f", 64))
					if mismatchErr == nil {
						t.Fatal("stale rollback digest accepted")
					}
					afterMismatch, observeErr := capacityKeySnapshot(ctx, client)
					if observeErr != nil || !reflect.DeepEqual(before, afterMismatch) {
						t.Fatal("stale rollback digest changed queue authority")
					}
					var fenceCount int64
					if pool.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence").Scan(&fenceCount) != nil || fenceCount != 1 {
						t.Fatal("stale rollback digest changed canonical fences")
					}
					applied, err = a.applyRollback(ctx, state, plan.Digest)
				}
				if err != nil {
					t.Fatal("native complete apply failed", err)
				}
				got, err := b0task.CanonicalJSON(applied, true)
				if err != nil {
					t.Fatal(err)
				}
				want, err := b0task.CanonicalJSON(v.Applied, true)
				if err != nil || string(got) != string(want) {
					t.Fatal("complete original apply reply differs", string(got), string(want), err)
				}
				actual, err := b0PlanEffectsSnapshot(ctx, client)
				if err != nil {
					t.Fatal(err)
				}
				// Replay captured actual Python Lua calls on this same Redis engine.
				// Redis versions differ in ZSCORE decimal spelling; raw guard
				// strings must still match exactly within one engine.
				original := capacityRedisFixture(t, ctx)
				b0SeedFullPlanRedis(t, ctx, original, v)
				if len(v.LuaCalls) == 0 {
					t.Fatal("actual original Lua calls absent")
				}
				for _, call := range v.LuaCalls {
					scriptSHA := sha1.Sum([]byte(b0CutoverQueueLua))
					if call.SHA != hex.EncodeToString(scriptSHA[:]) || call.KeyCount < 1 || call.KeyCount > len(call.Args) {
						t.Fatal("original Lua binding differs")
					}
					keys := []string{}
					for _, raw := range call.Args[:call.KeyCount] {
						key, ok := raw.(string)
						if !ok {
							t.Fatal("original key invalid")
						}
						keys = append(keys, key)
					}
					if _, err := original.Eval(ctx, b0CutoverQueueLua, keys, call.Args[call.KeyCount:]...).Result(); err != nil {
						t.Fatal("original Lua replay failed", err)
					}
				}
				reference, err := b0PlanEffectsSnapshot(ctx, original)
				if err != nil {
					t.Fatal("actual original effects unavailable", err)
				}
				actualBody, _ := json.Marshal(actual)
				expectedBody, _ := json.Marshal(reference)
				var actualValue, expectedValue any
				d := json.NewDecoder(strings.NewReader(string(actualBody)))
				d.UseNumber()
				if d.Decode(&actualValue) != nil {
					t.Fatal("actual effects unavailable")
				}
				d = json.NewDecoder(strings.NewReader(string(expectedBody)))
				d.UseNumber()
				if d.Decode(&expectedValue) != nil {
					t.Fatal("original effects unavailable")
				}
				normalizeB0RecordSnapshot(actualValue)
				normalizeB0RecordSnapshot(expectedValue)
				if !b0PlanJSONEqual(actualValue, expectedValue) {
					t.Fatal("complete original queue effects differ", string(actualBody), string(expectedBody))
				}
				var fences int64
				if pool.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence").Scan(&fences) != nil {
					t.Fatal("canonical effects unavailable")
				}
				wantFences := int64(1)
				if v.Operation == "rollback" {
					wantFences = 0
				}
				if fences != wantFences {
					t.Fatal("canonical fence effects differ")
				}
			}
			if producer.next != len(v.Requests) {
				t.Fatal("original producer preparation count differs")
			}
		})
	}
}

func normalizeB0RecordSnapshot(value any) {
	object, ok := value.(map[string]any)
	if !ok {
		return
	}
	for key, raw := range object {
		if !strings.HasSuffix(key, ":records") {
			continue
		}
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		records, ok := entry["value"].(map[string]any)
		if !ok {
			continue
		}
		for id, raw := range records {
			text, ok := raw.(string)
			if !ok {
				continue
			}
			var record any
			d := json.NewDecoder(strings.NewReader(text))
			d.UseNumber()
			if d.Decode(&record) == nil {
				records[id] = record
			}
		}
	}
}

func b0PlanEffectsSnapshot(ctx context.Context, client *redis.Client) (map[string]any, error) {
	keys, err := client.Keys(ctx, "*").Result()
	if err != nil {
		return nil, err
	}
	result := map[string]any{}
	for _, key := range keys {
		kind, err := client.Type(ctx, key).Result()
		if err != nil {
			return nil, err
		}
		var value any
		switch kind {
		case "string":
			value, err = client.Get(ctx, key).Result()
		case "hash":
			value, err = client.HGetAll(ctx, key).Result()
		case "set":
			members, e := client.SMembers(ctx, key).Result()
			sort.Strings(members)
			value, err = members, e
		case "zset":
			rows, e := client.ZRangeWithScores(ctx, key, 0, -1).Result()
			items := []any{}
			for _, row := range rows {
				items = append(items, []any{row.Member, row.Score})
			}
			value, err = items, e
		default:
			return nil, errB0Cutover
		}
		if err != nil {
			return nil, err
		}
		result[key] = map[string]any{"type": kind, "value": value}
	}
	return result, nil
}

func TestRealCompiledB0PlanAndApplyEmptyPendingRollback(t *testing.T) {
	if _, err := os.Lstat("/run/jobseek-lightpanda-producer/.activation-v1"); err == nil {
		t.Fatal("compiled private test cannot share an active producer sentinel")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	pool, dsn := b0PlanPostgresFixture(t, ctx)
	client := capacityRedisFixture(t, ctx)
	id := "00000000-0000-4000-8000-000000000001"
	if _, err := pool.Exec(ctx, `INSERT INTO lightpanda_b0_write_fence VALUES($1,'go','lightpanda-b0',7),($1,'go','other',7)`, id); err != nil {
		t.Fatal("private canonical fence seed failed")
	}
	binary := filepath.Join(t.TempDir(), "native-cutover")
	build := exec.CommandContext(ctx, "go", "build", "-o", binary, ".")
	if body, err := build.CombinedOutput(); err != nil {
		t.Fatal("compiled native CLI unavailable", string(body))
	}
	env := []string{"REDIS_URL=unix://" + client.Options().Addr, "LOCAL_DATABASE_URL=" + dsn, "CRAWLER_DB_ROLE=lightpanda-b0-activation", "LIGHTPANDA_B0_PRODUCER_MODE=off", "LIGHTPANDA_B0_QUEUE_NAMESPACE=compiled-plan-fixture", "LIGHTPANDA_B0_SHARD_ID=lightpanda-b0", "LIGHTPANDA_B0_ROUTING_EPOCH=7"}
	receipt := strings.Repeat("a", 64)
	plan := exec.CommandContext(ctx, binary, "--b0-cutover", "plan", "--operation", "rollback", "--cohort", "c1", "--receipt-state", "pending", "--source-receipt-sha256", receipt)
	plan.Env = env
	body, err := plan.CombinedOutput()
	var document struct {
		Digest         string
		Count          int
		Plan           map[string]any
		RollbackDigest string `json:"rollback_plan_digest"`
	}
	if err != nil || json.Unmarshal(body, &document) != nil || !capacityDigest.MatchString(document.Digest) || document.Count != 0 || document.RollbackDigest != document.Digest {
		t.Fatal("compiled pending plan ABI differs", string(body))
	}
	before, err := capacityKeySnapshot(ctx, client)
	if err != nil {
		t.Fatal(err)
	}
	stale := exec.CommandContext(ctx, binary, "--b0-cutover", "rollback", "--cohort", "c1", "--receipt-state", "pending", "--source-receipt-sha256", receipt, "--apply", "--expect-digest", strings.Repeat("f", 64))
	stale.Env = env
	if stale.Run() == nil {
		t.Fatal("compiled stale digest accepted")
	}
	after, err := capacityKeySnapshot(ctx, client)
	if err != nil || !reflect.DeepEqual(before, after) {
		t.Fatal("compiled stale digest changed queue authority")
	}
	apply := exec.CommandContext(ctx, binary, "--b0-cutover", "rollback", "--cohort", "c1", "--receipt-state", "pending", "--source-receipt-sha256", receipt, "--apply", "--expect-digest", document.Digest)
	apply.Env = env
	body, err = apply.CombinedOutput()
	var reply map[string]any
	if err != nil || json.Unmarshal(body, &reply) != nil || reply["operation"] != "rollback" || reply["digest"] != document.Digest || reply["write_fences_remaining"] != float64(0) {
		t.Fatal("compiled apply ABI differs", string(body))
	}
	var current, other int64
	if pool.QueryRow(ctx, "SELECT count(*) FILTER (WHERE shard_id='lightpanda-b0'),count(*) FILTER(WHERE shard_id='other') FROM lightpanda_b0_write_fence").Scan(&current, &other) != nil || current != 0 || other != 1 {
		t.Fatal("compiled canonical route fence scope changed")
	}
	// Redis committed before the SQL fence delete. A retry plans against that
	// retained tombstone and binds the original rollback digest.
	recover := exec.CommandContext(ctx, binary, "--b0-cutover", "plan", "--operation", "rollback", "--cohort", "c1", "--receipt-state", "pending", "--source-receipt-sha256", receipt)
	recover.Env = env
	body, err = recover.CombinedOutput()
	var recovery struct {
		Digest         string
		RollbackDigest string `json:"rollback_plan_digest"`
	}
	if err != nil || json.Unmarshal(body, &recovery) != nil || recovery.RollbackDigest != document.Digest || recovery.Digest == document.Digest {
		t.Fatal("compiled committed recovery digest differs", string(body))
	}
	retry := exec.CommandContext(ctx, binary, "--b0-cutover", "rollback", "--cohort", "c1", "--receipt-state", "pending", "--source-receipt-sha256", receipt, "--apply", "--expect-digest", recovery.Digest)
	retry.Env = env
	body, err = retry.CombinedOutput()
	if err != nil || json.Unmarshal(body, &reply) != nil || reply["rollback_plan_digest"] != document.Digest || reply["ready_restored"] != float64(0) {
		t.Fatal("compiled committed recovery reply differs", string(body))
	}
}
