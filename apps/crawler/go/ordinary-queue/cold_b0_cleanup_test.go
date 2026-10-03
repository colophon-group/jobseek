package queue

import (
	"context"
	"encoding/json"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func TestRealColdB0CleanupRequiresLiveSQLAndExactRestoredSource(t *testing.T) {
	for _, native := range []bool{false, true} {
		t.Run(strconv.FormatBool(native), func(t *testing.T) {
			ctx := context.Background()
			p, request := ordinaryRestorationFixture(t, native)
			plan, err := BuildColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, request, p.target)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := RetainColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, request, p.target, plan.digest); err != nil {
				t.Fatal(err)
			}
			before, canonical := forwardRedisSnapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
			if result, err := ObserveColdB0Cleanup(ctx, p.f.observer, p.f.client, plan.digest, request.SourceRevision, p.target); err == nil || result != nil {
				t.Fatal("cleanup observation admitted without live host SQL")
			}
			binding := HostColdSQLBinding{request.SourceRevision, strings.Repeat("b", 64), strings.Repeat("c", 64)}
			var escaped context.Context
			err = WithHostColdSQL(ctx, p.f.observer, binding, func(scoped context.Context, sql *HostColdSQL) error {
				escaped = scoped
				evidence, err := ObserveColdB0Cleanup(scoped, p.f.observer, p.f.client, plan.digest, request.SourceRevision, p.target)
				if err != nil || evidence == nil {
					t.Fatal("live cleanup observation", err)
				}
				var document coldB0CleanupDocument
				if json.Unmarshal([]byte(evidence.Payload()), &document) != nil || document.Binding != binding || document.HostSQLSHA256 != sql.SHA256() || string(document.HostSQL) != sql.Body() || document.SourceGoWriteFences != 0 || document.RetirementEpoch != request.RetirementEpoch || document.B0RestorationPlanSHA256 != request.B0RestorationPlanSHA256 || document.RuntimeAdmission || evidence.SHA256() != coldForwardBytesDigest(evidence.Payload()) {
					t.Fatal("cleanup observation did not bind live SQL/restoration")
				}
				for _, mode := range []string{"foreign_source", "missing_plan", "foreign_owner", "expiring_owner", "source_queue"} {
					source, digest := request.SourceRevision, plan.digest
					switch mode {
					case "foreign_source":
						source = strings.Repeat("f", 40)
					case "missing_plan":
						digest = strings.Repeat("f", 64)
					case "foreign_owner":
						if err := p.f.client.redis.HSet(scoped, "lightpanda-b0:producer-owner", "extra", "foreign").Err(); err != nil {
							t.Fatal(err)
						}
					case "expiring_owner":
						if err := p.f.client.redis.Do(scoped, "PEXPIRE", "lightpanda-b0:producer-owner", 600000).Err(); err != nil {
							t.Fatal(err)
						}
					case "source_queue":
						if err := p.f.client.redis.Set(scoped, p.target.keys()[1], "orphan", 0).Err(); err != nil {
							t.Fatal(err)
						}
					}
					if result, err := ObserveColdB0Cleanup(scoped, p.f.observer, p.f.client, digest, source, p.target); err == nil || result != nil {
						t.Fatal("substituted cleanup source admitted", mode)
					}
					p.f.client.redis.HDel(scoped, "lightpanda-b0:producer-owner", "extra")
					p.f.client.redis.Persist(scoped, "lightpanda-b0:producer-owner")
					p.f.client.redis.Del(scoped, p.target.keys()[1])
				}
				current, err := ObserveColdB0Cleanup(scoped, p.f.observer, p.f.client, plan.digest, request.SourceRevision, p.target)
				if err != nil || current.Payload() != evidence.Payload() {
					t.Fatal("same live backend observation changed or consumed authority", err)
				}
				return nil
			})
			if err != nil {
				t.Fatal(err)
			}
			if result, err := ObserveColdB0Cleanup(escaped, p.f.observer, p.f.client, plan.digest, request.SourceRevision, p.target); err == nil || result != nil {
				t.Fatal("past SQL receipt granted cleanup observation")
			}
			if canonical != coldB0CanonicalSnapshot(t, p) || !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) {
				t.Fatal("cleanup observation changed canonical rows or complete Redis values")
			}
		})
	}
}
