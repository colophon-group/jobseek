package queue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

func TestColdB0ForwardSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_b0_forward.sql")
	if err != nil || string(body) != coldB0ForwardSchema {
		t.Fatal("forward retention differs from actual migration")
	}
}

func TestRealColdB0ForwardRetainedDecoderBindsTasksToSource(t *testing.T) {
	p, r, control := forwardFixture(t)
	id := forwardFixtureLegacyPosting(t, p, true, "350.0001", true)
	plan, err := buildFixtureForward(t, p, r, control)
	if err != nil {
		t.Fatal(err)
	}
	for _, fault := range []string{"first_time", "millis", "score", "hint"} {
		t.Run(fault, func(t *testing.T) {
			var doc coldB0ForwardDocument
			if json.Unmarshal([]byte(plan.body), &doc) != nil {
				t.Fatal("retained manifest unavailable")
			}
			for i := range doc.Tasks {
				if doc.Tasks[i].PostingID != id {
					continue
				}
				switch fault {
				case "first_time":
					doc.Tasks[i].FirstTime = false
				case "millis":
					doc.Tasks[i].NextScrapeAtMS++
				case "score":
					doc.Tasks[i].LegacyScheduleScore = "350.0002"
				case "hint":
					doc.Tasks[i].Config["description_r2_hash"] = "8"
				}
			}
			// Valid canonical bytes and a matching self-digest cannot authorize
			// a different producer request than the retained source evidence.
			body, _ := json.Marshal(doc)
			h := sha256.Sum256(body)
			if _, err := decodeColdB0ForwardPlan(string(body), hex.EncodeToString(h[:])); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("retained task ceased matching original source", fault)
			}
		})
	}
}

// These fixtures prove real PostgreSQL/Redis source conservation and retained
// history, using opaque producer preparation decisions. They do not attest the
// producer binary/parser assignment; actual UID10001 UDS execution has its own
// required process integration. No runtime entry point accepts this override.
type forwardFixtureControl struct {
	p            publicationFixture
	afterPrepare func()
	prepareCalls int
}

func (f *forwardFixtureControl) Manifest(ctx context.Context, cohort string) (b0producer.Response, error) {
	count, err := f.p.f.client.redis.HLen(ctx, f.p.target.keys()[1]).Result()
	return b0producer.Response{Version: b0producer.Protocol, Outcome: "manifest", Reason: "manifest", Cohort: cohort, BoardSlugs: []string{"browser-use-careers"}, LifetimeOccupancy: count, LifetimeCapacity: 2048, LifetimeHeadroom: 2048 - count}, err
}
func (f *forwardFixtureControl) Prepare(ctx context.Context, t b0producer.Task) (b0producer.Response, error) {
	f.prepareCalls++
	r := b0producer.Response{Version: b0producer.Protocol, Outcome: "prepared", Reason: "prepared", BoardSlugs: []string{}, LifetimeCapacity: 2048, LifetimeHeadroom: 2048}
	request := b0producer.Request{Version: b0producer.Protocol, Operation: "prepare", Domain: t.Domain, PostingID: t.PostingID, Config: t.Config, NextScrapeAtMS: t.NextScrapeAtMS, Browser: t.Browser, FirstTime: t.FirstTime, OperatorTransfer: true, LegacyScheduleScore: t.LegacyScheduleScore}
	body, err := b0producer.EncodeRequest(request)
	if err != nil {
		return r, err
	}
	h := sha256.Sum256(body)
	r.PreparationDigest = hex.EncodeToString(h[:])
	r.PayloadSHA256 = strings.Repeat("b", 64)
	raw, err := f.p.f.client.redis.HGet(ctx, f.p.target.keys()[1], t.PostingID).Result()
	if err == nil {
		var stored coldB0RollbackRecord
		if json.Unmarshal([]byte(raw), &stored) != nil {
			return r, ErrProtocol
		}
		r.ExistingState, r.ExistingPayloadSHA256 = stored.State, stored.PayloadSHA256
		r.PayloadSHA256 = stored.PayloadSHA256
	} else if err != redis.Nil {
		return r, err
	}
	if f.afterPrepare != nil {
		callback := f.afterPrepare
		f.afterPrepare = nil
		callback()
	}
	return r, nil
}

func forwardFixture(t *testing.T) (publicationFixture, ColdB0ForwardRequest, *forwardFixtureControl) {
	t.Helper()
	return forwardFixtureWithMetadata(t, "{}")
}

func forwardFixtureWithMetadata(t *testing.T, metadata string) (publicationFixture, ColdB0ForwardRequest, *forwardFixtureControl) {
	return forwardFixtureSource(t, metadata, strings.Repeat("a", 40))
}

func forwardFixtureSource(t *testing.T, metadata, source string) (publicationFixture, ColdB0ForwardRequest, *forwardFixtureControl) {
	t.Helper()
	p := realPublicationSource(t, metadata, source)
	id := rollbackFixturePosting(t, p)
	if _, err := p.f.observer.Exec(context.Background(), "UPDATE job_posting SET source_url='https://jobs.example.test/posting' WHERE id=$1::uuid", id); err != nil {
		t.Fatal(err)
	}
	r := ColdB0ForwardRequest{p.intent, p.spec.SourceRevision, p.plan.Epoch(), p.plan.digest}
	return p, r, &forwardFixtureControl{p: p}
}

func buildFixtureForward(t *testing.T, p publicationFixture, r ColdB0ForwardRequest, control *forwardFixtureControl) (*ColdB0ForwardPlan, error) {
	t.Helper()
	var result *ColdB0ForwardPlan
	err := coldTransitionTransaction(context.Background(), p.f.observer, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = deriveColdB0ForwardPlan(ctx, tx, p.f.client, control, r, p.target)
		return err
	})
	return result, err
}

func retainFixtureForward(p publicationFixture, r ColdB0ForwardRequest, control *forwardFixtureControl, digest string) (*ColdB0ForwardPlan, error) {
	var result *ColdB0ForwardPlan
	err := coldTransitionTransaction(context.Background(), p.f.observer, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = retainColdB0ForwardPlan(ctx, tx, p.f.client, control, r, p.target, digest)
		return err
	})
	return result, err
}

func forwardFixtureLegacyPosting(t *testing.T, p publicationFixture, first bool, score string, queued bool) string {
	t.Helper()
	ctx := context.Background()
	id := ordinaryID(t)
	url := "https://jobs.example.test/é/" + id
	_, err := p.f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,description_r2_hash,next_scrape_at)
 VALUES($1::uuid,$2::uuid,$3::uuid,$4,-9223372036854775808,'2031-01-02 03:04:05.100001+00')`, id, p.f.company, p.target.document.Boards[0].ID, url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", id)
	})
	if queued {
		config := map[string]string{"domain": "jobs.example.test", "board_id": p.target.document.Boards[0].ID, "source_url": url, "description_r2_hash": "7", "scrape_step": "0"}
		if err := p.f.client.redis.HSet(ctx, "scrape:"+id, config).Err(); err != nil {
			t.Fatal(err)
		}
		prefix := "scrapes_browser:"
		if first {
			prefix = "ft_scrapes_browser:"
		}
		value, err := strconv.ParseFloat(score, 64)
		if err != nil {
			t.Fatal(err)
		}
		if err := p.f.client.redis.ZAdd(ctx, prefix+"jobs.example.test", redis.Z{Member: id, Score: value}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	return id
}

func TestRealColdB0ForwardConservesExactSchedulesHintsAndPrunedWork(t *testing.T) {
	p, r, control := forwardFixture(t)
	first := forwardFixtureLegacyPosting(t, p, true, "350.0001", true)
	recurring := forwardFixtureLegacyPosting(t, p, false, "1925089445.123001", true)
	pruned := forwardFixtureLegacyPosting(t, p, false, "0", false)
	ctx := context.Background()
	before, canonical := forwardRedisSnapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
	plan, err := buildFixtureForward(t, p, r, control)
	if err != nil {
		t.Fatal("native forward preparation failed", err)
	}
	if plan.document.NewRecordCount != 2 || plan.document.ProjectedOccupancy != 3 || !reflect.DeepEqual(plan.document.UnqueuedPostingIDs, []string{pruned}) || len(plan.document.Tasks) != 3 {
		t.Fatal("native plan recreated pruned work or lost occupancy")
	}
	for _, task := range plan.document.Tasks {
		if task.PostingID == first || task.PostingID == recurring {
			if task.Config["description_r2_hash"] != "7" || task.PostgresHash != "-9223372036854775808" || task.Config["scrape_interval_hours"] != "24" {
				t.Fatal("legacy hint replaced PG truth or canonical interval lost")
			}
			if task.PostingID == first && (!task.FirstTime || task.LegacyScheduleScore != "350.0001" || task.NextScrapeAtMS != 350001) {
				t.Fatal("first transfer score/ceiling lost")
			}
			if task.PostingID == recurring && (task.FirstTime || task.LegacyScheduleScore != "1925089445.123001" || task.NextScrapeAtMS != 1925089445124) {
				t.Fatal("future fractional source score/ceiling lost")
			}
		}
	}
	if decoded, err := decodeColdB0ForwardPlan(plan.body, plan.digest); err != nil || decoded.body != plan.body {
		t.Fatal("complete native manifest failed retained validation", err)
	}
	retained, err := retainFixtureForward(p, r, control, plan.digest)
	if err != nil || retained.body != plan.body {
		t.Fatal("approved full manifest did not commit", err)
	}
	if _, err := retainFixtureForward(p, r, control, plan.digest); err != nil {
		t.Fatal("unchanged approval not idempotent", err)
	}
	observed, err := InspectColdB0ForwardPlan(ctx, p.f.observer, plan.digest, r.SourceRevision)
	if err != nil || observed.body != plan.body {
		t.Fatal("read-only retained inspection lost exact intent", err)
	}
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reserved" {
		t.Fatal("planning/retention replayed queue/canonical data or selected ownership")
	}
	for _, query := range []string{"DELETE FROM crawler_ownership_b0_forward WHERE plan_sha256=$1", "UPDATE crawler_ownership_b0_forward SET payload=payload||' ' WHERE plan_sha256=$1", "UPDATE crawler_ownership_b0_forward SET routing_epoch=routing_epoch+1 WHERE plan_sha256=$1"} {
		if _, err := p.f.observer.Exec(ctx, query, plan.digest); err == nil {
			t.Fatal("approved forward history was rewritten")
		}
	}
	var schemaBefore, schemaAfter string
	if err := p.f.observer.QueryRow(ctx, "SELECT version_num FROM alembic_version").Scan(&schemaBefore); err != nil {
		t.Fatal(err)
	}
	downgrade := exec.Command("uv", "run", "--frozen", "--no-sync", "alembic", "-c", "src/migrations/alembic.ini", "downgrade", "0042")
	downgrade.Dir = "../.."
	downgrade.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+p.f.dsn)
	if output, err := downgrade.CombinedOutput(); err == nil || !strings.Contains(string(output), "crawler_ownership_history_retained") {
		t.Fatal("actual downgrade did not protect retained forward history")
	}
	if err := p.f.observer.QueryRow(ctx, "SELECT version_num FROM alembic_version").Scan(&schemaAfter); err != nil || schemaAfter != schemaBefore {
		t.Fatal("refused downgrade changed installed schema")
	}
	if observed, err := InspectColdB0ForwardPlan(ctx, p.f.observer, plan.digest, r.SourceRevision); err != nil || observed.body != plan.body {
		t.Fatal("refused downgrade lost exact approved history", err)
	}
}

func TestRealColdB0ForwardRetainsUnscheduledTerminalWithoutRecreatingIt(t *testing.T) {
	p, r, control := forwardFixture(t)
	rollbackFixtureState(t, p, "terminal")
	ctx := context.Background()
	var terminal string
	if err := p.f.observer.QueryRow(ctx, "SELECT id::text FROM job_posting WHERE board_id=$1::uuid", p.target.document.Boards[0].ID).Scan(&terminal); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1::uuid", terminal); err != nil {
		t.Fatal(err)
	}
	queued := forwardFixtureLegacyPosting(t, p, false, "1925089445.123001", true)
	plan, err := buildFixtureForward(t, p, r, control)
	if err != nil || len(plan.document.Tasks) != 1 || plan.document.Tasks[0].PostingID != queued || !reflect.DeepEqual(plan.document.RetainedTerminalIDs, []string{terminal}) || plan.document.ProjectedOccupancy != 2 {
		t.Fatal("terminal lifetime authority was discarded or recreated", err)
	}
	if _, err := retainFixtureForward(p, r, control, plan.digest); err != nil {
		t.Fatal("terminal conservation manifest did not retain", err)
	}
}

func forwardRedisSnapshot(t *testing.T, c *Client) map[string]string {
	t.Helper()
	ctx := context.Background()
	keys, err := c.redis.Keys(ctx, "*").Result()
	if err != nil {
		t.Fatal(err)
	}
	result := map[string]string{}
	for _, key := range keys {
		kind, err := c.redis.Type(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		var value any
		switch kind {
		case "string":
			var v string
			v, err = c.redis.Get(ctx, key).Result()
			value = []byte(v)
		case "hash":
			var fields map[string]string
			fields, err = c.redis.HGetAll(ctx, key).Result()
			v := map[string][]byte{}
			for k, b := range fields {
				v[hex.EncodeToString([]byte(k))] = []byte(b)
			}
			value = v
		case "zset":
			var v []redis.Z
			v, err = c.redis.ZRangeWithScores(ctx, key, 0, -1).Result()
			for i := range v {
				s, ok := v[i].Member.(string)
				if !ok {
					t.Fatal("private member observation failed")
				}
				v[i].Member = []byte(s)
			}
			value = v
		case "set":
			var members []string
			members, err = c.redis.SMembers(ctx, key).Result()
			sort.Strings(members)
			v := [][]byte{}
			for _, s := range members {
				v = append(v, []byte(s))
			}
			value = v
		case "list":
			var members []string
			members, err = c.redis.LRange(ctx, key, 0, -1).Result()
			v := [][]byte{}
			for _, s := range members {
				v = append(v, []byte(s))
			}
			value = v
		default:
			t.Fatal("unexpected private source observation type")
		}
		if err != nil {
			t.Fatal(err)
		}
		body, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		ttl, err := c.redis.PTTL(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		result[key] = kind + ":" + strconv.FormatBool(ttl > 0) + ":" + string(body)
	}
	return result
}

func TestRealColdB0ForwardRetainedInspectionDoesNotAdoptFreshAllocatorOrLocks(t *testing.T) {
	p, r, control := forwardFixture(t)
	plan, err := buildFixtureForward(t, p, r, control)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := retainFixtureForward(p, r, control, plan.digest); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	lock, err := p.f.observer.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Release()
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_lock($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	held := true
	defer func() {
		if held {
			_, _ = lock.Exec(context.Background(), "SELECT pg_advisory_unlock($1)", OrdinaryLeaseBarrier)
		}
	}()
	bounded, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	if observed, err := InspectColdB0ForwardPlan(bounded, p.f.observer, plan.digest, r.SourceRevision); err != nil || observed.digest != plan.digest {
		t.Fatal("read-only inspection acquired ownership barrier", err)
	}
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	held = false
	if _, err := p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')"); err != nil {
		t.Fatal(err)
	}
	if _, err := retainFixtureForward(p, r, control, plan.digest); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("retention adopted newer allocator", err)
	}
	if observed, err := InspectColdB0ForwardPlan(ctx, p.f.observer, plan.digest, r.SourceRevision); err != nil || observed.body != plan.body {
		t.Fatal("retained history lost after high-water changed", err)
	}
}

func TestRealColdB0ForwardCannotReplaceApprovedManifestForSameIntent(t *testing.T) {
	p, r, control := forwardFixture(t)
	plan, err := buildFixtureForward(t, p, r, control)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := retainFixtureForward(p, r, control, plan.digest); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(context.Background(), "UPDATE job_posting SET next_scrape_at=next_scrape_at+interval '1 hour' WHERE company_id=$1::uuid", p.f.company); err != nil {
		t.Fatal(err)
	}
	changed, err := buildFixtureForward(t, p, r, control)
	if err != nil || changed.digest == plan.digest {
		t.Fatal("canonical schedule change was not bound", err)
	}
	if _, err := retainFixtureForward(p, r, control, changed.digest); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("same intent changed approved manifest", err)
	}
	observed, err := InspectColdB0ForwardPlan(context.Background(), p.f.observer, plan.digest, r.SourceRevision)
	if err != nil || observed.body != plan.body {
		t.Fatal("original immutable manifest not retained", err)
	}
}

func TestRealColdB0ForwardRejectsLostOrDriftingAuthorityBeforeRetention(t *testing.T) {
	for _, fault := range []string{"pg_lease", "wrong_epoch", "wrong_plan", "wrong_source", "wrong_intent", "disabled_board", "changed_config", "legacy_simple", "duplicate_membership", "missing_hash", "hash_drift", "hash_expiry", "schedule_expiry", "suffix_inflight", "suffix_deadletter", "unexpected_projection", "unexpected_witness", "producer_drift", "late_legacy_drift", "b0_inflight", "b0_dead", "unscheduled_ready", "bad_source_url", "wrong_approval"} {
		t.Run(fault, func(t *testing.T) {
			p, r, control := forwardFixture(t)
			id := forwardFixtureLegacyPosting(t, p, false, "1925089445.123001", true)
			ctx := context.Background()
			var err error
			switch fault {
			case "pg_lease":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET leased_until=now()+interval '1 hour' WHERE id=$1::uuid", id)
			case "wrong_epoch":
				r.RoutingEpoch++
			case "wrong_plan":
				r.OrdinaryPlanSHA256 = strings.Repeat("9", 64)
			case "wrong_source":
				r.SourceRevision = strings.Repeat("9", 40)
			case "wrong_intent":
				r.IntentSHA256 = strings.Repeat("9", 64)
			case "disabled_board":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", p.target.document.Boards[0].ID)
			case "changed_config":
				err = p.f.client.redis.HSet(ctx, "board:"+p.target.document.Boards[0].ID, "metadata", `{"changed":true}`).Err()
			case "legacy_simple":
				err = p.f.client.redis.ZAdd(ctx, "scrapes_simple:jobs.example.test", redis.Z{Member: id, Score: 1}).Err()
			case "duplicate_membership":
				err = p.f.client.redis.ZAdd(ctx, "ft_scrapes_browser:jobs.example.test", redis.Z{Member: id, Score: 1}).Err()
			case "missing_hash":
				err = p.f.client.redis.Del(ctx, "scrape:"+id).Err()
			case "hash_drift":
				err = p.f.client.redis.HSet(ctx, "scrape:"+id, "source_url", "https://secret.example.test/").Err()
			case "hash_expiry":
				err = p.f.client.redis.Expire(ctx, "scrape:"+id, time.Hour).Err()
			case "schedule_expiry":
				err = p.f.client.redis.Expire(ctx, "scrapes_browser:jobs.example.test", time.Hour).Err()
			case "suffix_inflight":
				err = p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Member: "scrape|other.example.test|" + id, Score: 1}).Err()
			case "suffix_deadletter":
				err = p.f.client.redis.ZAdd(ctx, "deadletter:browser", redis.Z{Member: "scrape|other.example.test|" + id, Score: 1}).Err()
			case "unexpected_projection":
				err = p.f.client.redis.Set(ctx, ownershipProjectionKey, "secret", 0).Err()
			case "unexpected_witness":
				err = p.f.client.redis.Set(ctx, coldPublicationKey, "secret", 0).Err()
			case "producer_drift":
				err = p.f.client.redis.HSet(ctx, "lightpanda-b0:producer-owner", "cohort", "cdom").Err()
			case "late_legacy_drift":
				control.afterPrepare = func() {
					if err := p.f.client.redis.HSet(ctx, "scrape:"+id, "description_r2_hash", "8").Err(); err != nil {
						t.Error(err)
					}
				}
			case "b0_inflight":
				rollbackFixtureState(t, p, "inflight")
			case "b0_dead":
				rollbackFixtureState(t, p, "dead")
			case "unscheduled_ready":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL WHERE id<>$1::uuid AND company_id=$2::uuid", id, p.f.company)
			case "bad_source_url":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET source_url='https://user:secret@jobs.example.test/posting' WHERE id=$1::uuid", id)
			}
			if err != nil {
				t.Fatal("owned drift injection failed", err)
			}
			var approved string
			if fault == "wrong_approval" {
				approved = strings.Repeat("9", 64)
			}
			var result *ColdB0ForwardPlan
			if approved != "" {
				result, err = retainFixtureForward(p, r, control, approved)
			} else {
				result, err = buildFixtureForward(t, p, r, control)
			}
			if err == nil || result != nil || strings.Contains(err.Error(), "secret") {
				t.Fatal("drifting native forward authority accepted/exposed", err)
			}
			var count int
			if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM crawler_ownership_b0_forward WHERE intent_sha256=$1", p.intent).Scan(&count); err != nil || count != 0 {
				t.Fatal("failed observation retained an approval")
			}
		})
	}
}

func TestColdB0ForwardCanonicalRequestAndNumericBounds(t *testing.T) {
	r := ColdB0ForwardRequest{strings.Repeat("a", 64), strings.Repeat("b", 40), 3, strings.Repeat("c", 64)}
	body, _ := json.Marshal(r)
	h := sha256.Sum256(body)
	if got, err := DecodeColdB0ForwardRequest(string(body), hex.EncodeToString(h[:])); err != nil || got != r {
		t.Fatal("canonical forward request rejected", err)
	}
	for _, bad := range []string{string(body) + " ", strings.TrimSuffix(string(body), "}") + `,"routing_epoch":3}`, strings.TrimSuffix(string(body), "}") + `,"secret":"input"}`, strings.Replace(string(body), `"routing_epoch":3`, `"routing_epoch":0`, 1)} {
		h := sha256.Sum256([]byte(bad))
		if _, err := DecodeColdB0ForwardRequest(bad, hex.EncodeToString(h[:])); !errors.Is(err, ErrConfiguration) {
			t.Fatal("unsafe protected request accepted")
		}
	}
	for _, test := range []struct {
		score string
		ms    int64
	}{{"0", 0}, {"0.000001", 1}, {"350.0001", 350001}, {"1925089445.100001", 1925089445101}, {"9999999999.999", 9999999999999}} {
		if got, err := coldB0ForwardMillis(test.score); err != nil || got != test.ms {
			t.Fatal("exact forward ceiling failed", test.score, got, err)
		}
	}
	for _, bad := range []string{"-1", "nan", "inf", "10000000000", "1/2"} {
		if _, err := coldB0ForwardMillis(bad); err == nil {
			t.Fatal("unsafe forward time accepted")
		}
	}
}
