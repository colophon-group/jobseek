package queue

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"errors"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/redis/go-redis/v9"
)

func coldB0CanonicalSnapshot(t *testing.T, p publicationFixture) string {
	t.Helper()
	var body string
	err := p.f.observer.QueryRow(context.Background(), `SELECT jsonb_build_object(
 'company',(SELECT to_jsonb(c) FROM company c WHERE id=$1::uuid),
 'boards',(SELECT jsonb_agg(to_jsonb(b) ORDER BY id) FROM job_board b WHERE company_id=$1::uuid),
 'postings',(SELECT jsonb_agg(to_jsonb(j) ORDER BY id) FROM job_posting j WHERE company_id=$1::uuid),
 'descriptions',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.posting_id,d.locale) FROM descriptions d JOIN job_posting j ON j.id=d.posting_id WHERE j.company_id=$1::uuid),
 'receipts',(SELECT jsonb_agg(to_jsonb(f) ORDER BY f.task_kind,f.task_id) FROM ordinary_worker_write_fence f JOIN job_board b ON b.id=f.board_id WHERE b.company_id=$1::uuid))::text`, p.f.company).Scan(&body)
	if err != nil {
		t.Fatal("complete owned canonical B0/ordinary snapshot unavailable")
	}
	return body
}

func rollbackFixturePosting(t *testing.T, p publicationFixture) string {
	t.Helper()
	ctx := context.Background()
	ids, err := p.f.client.redis.HKeys(ctx, p.target.keys()[1]).Result()
	if err != nil || len(ids) != 1 {
		t.Fatal("actual B0 record absent")
	}
	id := ids[0]
	_, err = p.f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,description_r2_hash,next_scrape_at)
 VALUES($1::uuid,$2::uuid,$3::uuid,$4,-9223372036854775808,'2031-01-02 03:04:05.100001+00')`, id, p.f.company, p.target.document.Boards[0].ID, "https://jobs.example.test/"+id)
	if err != nil {
		t.Fatal("canonical B0 posting unavailable", err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", id)
	})
	return id
}

func rollbackFixtureRequest(t *testing.T, p publicationFixture) ColdB0RollbackRequest {
	t.Helper()
	s := reversalSpec(t, p, "reserved")
	digest, err := BeginColdOwnershipReversal(context.Background(), p.f.observer, s)
	if err != nil {
		t.Fatal(err)
	}
	state, err := ReserveColdReversalEpoch(context.Background(), p.f.observer, digest, s.SourceRevision)
	if err != nil {
		t.Fatal(err)
	}
	return ColdB0RollbackRequest{ReversalSHA256: digest, SourceRevision: s.SourceRevision, RetirementEpoch: state.retirement, B0SourceEpoch: p.plan.Epoch(), SourceReceiptSHA256: strings.Repeat("8", 64)}
}

func rollbackFixtureState(t *testing.T, p publicationFixture, state string) {
	t.Helper()
	if state == "ready" {
		return
	}
	ctx := context.Background()
	args := p.target.auditArguments(p.plan.Epoch())
	args[0] = "claim_next"
	args[7] = "600000"
	if state == "dead" {
		args[7] = "1"
	}
	now, err := p.f.client.redis.Time(ctx).Result()
	if err != nil {
		t.Fatal(err)
	}
	args[6] = strconv.FormatInt(now.UnixMilli(), 10)
	reply, err := p.f.client.redis.Eval(ctx, p.target.lua, p.target.keys(), args...).Slice()
	if err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("actual B0 claim failed", reply, err)
	}
	if state == "inflight" {
		return
	}
	// Bind the actual claimed native attempt before its real queue terminal
	// transition. Planning must retain this historical DB fence without replay.
	_, err = p.f.observer.Exec(ctx, "SELECT public.jobseek_lightpanda_b0_activate_write_fence($1::uuid,$2,$3,'go',$4,$5,$6)", reply[3], p.target.document.ShardID, p.plan.Epoch(), int64(3), reply[7], reply[4])
	if err != nil {
		t.Fatal("actual B0 SQL fence unavailable", err)
	}
	args = p.target.auditArguments(p.plan.Epoch())
	if state == "dead" {
		time.Sleep(5 * time.Millisecond)
		args[0] = "reap_expired"
		args[9] = "1"
	} else {
		args[0] = "complete"
		args[4] = reply[3]
		args[5] = reply[6]
		args[6] = reply[4]
		args[11] = reply[7]
		legacy := sha1.Sum([]byte(reply[8].(string)))
		args[12] = hex.EncodeToString(legacy[:])
		args[16] = reply[5]
	}
	reply, err = p.f.client.redis.Eval(ctx, p.target.lua, p.target.keys(), args...).Slice()
	if err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("actual B0 lifecycle failed", reply, err)
	}
	key := p.target.keys()[5]
	if state == "dead" {
		key = p.target.keys()[4]
	}
	if p.f.client.redis.SCard(ctx, key).Val() != 1 {
		t.Fatal("actual B0 state absent")
	}
}

func TestRealColdB0RollbackDerivesCanonicalAndPreservesTransferIntent(t *testing.T) {
	for _, state := range []string{"ready", "terminal", "dead"} {
		for _, fault := range []string{"changed", "first_time", "disabled", "inactive", "gone", "null_due", "missing"} {
			t.Run(state+"/"+fault, func(t *testing.T) {
				p := realPublication(t)
				id := rollbackFixturePosting(t, p)
				rollbackFixtureState(t, p, state)
				ctx := context.Background()
				var err error
				switch fault {
				case "changed":
					_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET source_url=$2 WHERE id=$1::uuid", id, "https://changed.example.test/"+id)
					if err == nil {
						_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET scraper_needs_browser=false,scrape_interval_hours=48 WHERE id=$1::uuid", p.target.document.Boards[0].ID)
					}
				case "first_time":
					_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET description_r2_hash=NULL WHERE id=$1::uuid", id)
				case "disabled":
					_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", p.target.document.Boards[0].ID)
				case "inactive":
					_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", id)
				case "gone":
					_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET board_status='gone' WHERE id=$1::uuid", p.target.document.Boards[0].ID)
				case "null_due":
					_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1::uuid", id)
				case "missing":
					_, err = p.f.observer.Exec(ctx, "DELETE FROM job_posting WHERE id=$1::uuid", id)
				}
				if err != nil {
					t.Fatal("canonical source change failed", err)
				}
				request := rollbackFixtureRequest(t, p)
				before, canonical := snapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
				plan, err := BuildColdB0RollbackPlan(ctx, p.f.observer, p.f.client, request, p.target)
				if err != nil {
					t.Fatal("native canonical rollback rejected", err)
				}
				if state != "ready" && fault != "missing" && !reflect.DeepEqual(plan.document.FenceTaskIDs, []string{id}) {
					t.Fatal("completed B0 SQL fence lost")
				}
				entry := plan.document.RedisPlan[id]
				if fault != "changed" && fault != "first_time" {
					if entry.Action != "drop" || entry.Config != nil || entry.FirstTime != nil {
						t.Fatal("ineligible canonical work rescheduled")
					}
				} else {
					if entry.Action != "schedule" || entry.FirstTime == nil || entry.Config["board_id"] != p.target.document.Boards[0].ID || entry.Config["scrape_step"] != "0" {
						t.Fatal("canonical schedule missing")
					}
					if fault == "changed" {
						if entry.Domain != "changed.example.test" || entry.Config["scrape_interval_hours"] != "48" || entry.Config["description_r2_hash"] != "-9223372036854775808" {
							t.Fatal("stale cached source/config used")
						}
						if state == "ready" {
							if entry.WorkerType != "browser" || !*entry.FirstTime || entry.Score != "0.000" {
								t.Fatal("ready transfer intent/score lost")
							}
						} else {
							if entry.WorkerType != "simple" || *entry.FirstTime || entry.Score != "1925089445.100001" {
								t.Fatal("terminal/dead canonical due/lane lost", entry.Score)
							}
						}
					} else if !*entry.FirstTime || (state == "ready" && entry.Score != "0.000") || (state != "ready" && entry.Score != "0") || entry.Config["description_r2_hash"] != "" {
						t.Fatal("never-successful intent lost")
					}
				}
				retry, err := BuildColdB0RollbackPlan(ctx, p.f.observer, p.f.client, request, p.target)
				if err != nil || retry.body != plan.body || retry.digest != plan.digest {
					t.Fatal("unchanged canonical plan not deterministic")
				}
				if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
					t.Fatal("planning mutated queue/canonical/future state")
				}
				// In this owned fixture only, exercise the unchanged actual lifecycle
				// Lua against the native plan. This does not release the joint journal,
				// clear DB fences/sentinels or implement durable host restoration.
				body, _ := json.Marshal(plan.document.RedisPlan)
				args := p.target.auditArguments(request.B0SourceEpoch)
				args[0] = "rollback_legacy"
				args[15] = plan.digest
				args[18] = string(body)
				args[22] = request.SourceReceiptSHA256
				reply, err := p.f.client.redis.Eval(ctx, p.target.lua, p.target.keys(), args...).Slice()
				if err != nil || len(reply) != 12 || reply[0] != "accepted" {
					t.Fatal("native plan incompatible with actual rollback", reply, err)
				}
				if canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reversing" {
					t.Fatal("compatibility fixture released claims/replayed canonical effects")
				}
				if entry.Action == "schedule" {
					prefix := "scrapes_"
					if *entry.FirstTime {
						prefix = "ft_scrapes_"
					}
					score, err := p.f.client.redis.ZScore(ctx, prefix+entry.WorkerType+":"+entry.Domain, id).Result()
					expected, _ := strconv.ParseFloat(entry.Score, 64)
					if err != nil || score != expected {
						t.Fatal("actual rollback lost due/intent")
					}
					config, err := p.f.client.redis.HGetAll(ctx, "scrape:"+id).Result()
					if err != nil || !reflect.DeepEqual(config, entry.Config) {
						t.Fatal("actual rollback retained stale config")
					}
				} else if p.f.client.redis.Exists(ctx, "scrape:"+id).Val() != 0 {
					t.Fatal("dropped task kept scrape config")
				}
			})
		}
	}
}

func TestRealColdB0RollbackRejectsUnsafeSourcesBeforeEffects(t *testing.T) {
	for _, fault := range []string{"b0_inflight", "pg_lease", "suffix_inflight", "suffix_deadletter", "wrong_route", "wrong_retirement", "wrong_target", "expired_owner", "missing_guard", "corrupt_index", "later_epoch", "bad_url"} {
		t.Run(fault, func(t *testing.T) {
			p := realPublication(t)
			id := rollbackFixturePosting(t, p)
			ctx := context.Background()
			if fault == "b0_inflight" {
				rollbackFixtureState(t, p, "inflight")
			}
			request := rollbackFixtureRequest(t, p)
			target := p.target
			var err error
			switch fault {
			case "pg_lease":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET leased_until=now()+interval '1 hour' WHERE id=$1::uuid", id)
			case "suffix_inflight":
				err = p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Member: "scrape|old.example.test|" + id, Score: 1}).Err()
			case "suffix_deadletter":
				err = p.f.client.redis.ZAdd(ctx, "deadletter:browser", redis.Z{Member: "scrape|old.example.test|" + id, Score: 1}).Err()
			case "wrong_route":
				request.B0SourceEpoch++
			case "wrong_retirement":
				request.RetirementEpoch++
			case "wrong_target":
				copy := *p.target
				copy.digest = strings.Repeat("9", 64)
				target = &copy
			case "expired_owner":
				err = p.f.client.redis.PExpire(ctx, "lightpanda-b0:producer-owner", time.Hour).Err()
			case "missing_guard":
				err = p.f.client.redis.HDel(ctx, "lightpanda-b0:legacy-guard", id).Err()
			case "corrupt_index":
				err = p.f.client.redis.ZRem(ctx, p.target.keys()[2], id).Err()
			case "later_epoch":
				_, err = p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')")
			case "bad_url":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET source_url=$2 WHERE id=$1::uuid", id, "https://user:secret@changed.example.test/"+id)
			}
			if err != nil {
				t.Fatal("fault installation failed", err)
			}
			before, canonical := snapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
			if _, err := BuildColdB0RollbackPlan(ctx, p.f.observer, p.f.client, request, target); err == nil || strings.Contains(err.Error(), "secret") {
				t.Fatal("unsafe rollback source admitted/exposed input")
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("rejection changed canonical/queue state")
			}
		})
	}
}

func TestColdB0RollbackRejectsMissingExplicitIdentity(t *testing.T) {
	if _, err := BuildColdB0RollbackPlan(context.Background(), nil, nil, ColdB0RollbackRequest{}, nil); !errors.Is(err, ErrConfiguration) {
		t.Fatal("missing identity accepted")
	}
	for _, raw := range []string{"http://jobs.example.test/x", "https://jobs.example.test:444/x", "https://user:secret@jobs.example.test/x", "https://jobs.example.test/x#fragment", "https://-bad.example/x", "https://jobs.example.test/space path", "https://jobs.example.test/" + strings.Repeat("x", 16384)} {
		if _, err := coldRollbackSourceDomain(raw); !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("unsafe source URL accepted")
		}
	}
	if domain, err := coldRollbackSourceDomain("https://Bücher.example/x"); err != nil || domain != "xn--bcher-kva.example" {
		t.Fatal("canonical IDNA source rejected")
	}
}

func TestRealColdB0RollbackAfterActiveCandidateDriftAndWitnessLoss(t *testing.T) {
	for _, fault := range []string{"disabled", "changed", "joint_witness_lost", "redis_lost"} {
		t.Run(fault, func(t *testing.T) {
			p := realPublication(t)
			rollbackFixturePosting(t, p)
			activateJointFixture(t, p)
			ctx := context.Background()
			var err error
			switch fault {
			case "disabled":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id IN ($1::uuid,$2::uuid)", p.plan.document.Members[0].BoardID, p.target.document.Boards[0].ID)
			case "changed":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET scrape_interval_hours=48 WHERE id IN ($1::uuid,$2::uuid)", p.plan.document.Members[0].BoardID, p.target.document.Boards[0].ID)
			case "joint_witness_lost":
				err = p.f.client.redis.Del(ctx, coldPublicationKey).Err()
			case "redis_lost":
				err = p.f.client.redis.FlushDB(ctx).Err() // Validated wholly owned fixture.
			}
			if err != nil {
				t.Fatal(err)
			}
			s := reversalSpec(t, p, "active")
			digest, err := BeginColdOwnershipReversal(ctx, p.f.observer, s)
			if err != nil {
				t.Fatal(err)
			}
			state, err := ReserveColdReversalEpoch(ctx, p.f.observer, digest, s.SourceRevision)
			if err != nil {
				t.Fatal(err)
			}
			request := ColdB0RollbackRequest{ReversalSHA256: digest, SourceRevision: s.SourceRevision, RetirementEpoch: state.retirement, B0SourceEpoch: p.plan.Epoch(), SourceReceiptSHA256: strings.Repeat("8", 64)}
			before, canonical := snapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
			_, err = BuildColdB0RollbackPlan(ctx, p.f.observer, p.f.client, request, p.target)
			if fault == "redis_lost" {
				if !errors.Is(err, ErrAuthorityLost) {
					t.Fatal("lost task authority reconstructed automatically", err)
				}
			} else if err != nil {
				t.Fatal("damaged candidate cannot prepare restoration", err)
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reversing" {
				t.Fatal("planning repaired witness/released claims/replayed data")
			}
		})
	}
}

func TestColdB0RollbackGuardPreservesExactDecimalAndRejectsUnsafeRange(t *testing.T) {
	target := &ColdB0Target{document: coldB0Document{Namespace: "native-fixture", ShardID: "lightpanda-b0"}}
	task := b0task.Task{Envelope: b0task.Envelope{BoardID: "00000000-0000-4000-8000-000000000001", Domain: "jobs.example.test", ShardID: "lightpanda-b0", RoutingEpoch: 146}}
	base := "native-fixture|lightpanda-b0|146|" + task.Envelope.BoardID + "|jobs.example.test|recurring_browser|"
	for _, score := range []string{"0.000", "1925089445.100001", "1e-10", "9999999999.999"} {
		kind, got, err := coldRollbackGuard(base+score, target, task)
		if err != nil || got != score || kind != "recurring_browser" {
			t.Fatal("exact transfer score lost", err)
		}
	}
	for _, score := range []string{"-0", "NaN", "1e-999", "9999999999.9991", "01", "1|extra"} {
		if _, _, err := coldRollbackGuard(base+score, target, task); !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("unsafe transfer score accepted")
		}
	}
}
