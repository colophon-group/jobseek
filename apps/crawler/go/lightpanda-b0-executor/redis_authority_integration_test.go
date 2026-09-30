//go:build integration

package executor

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// The fixture driver alone owns Redis credentials. The installed executable
// and same-UID client receive none. This speaks the unchanged reviewed Lua ABI;
// it does not substitute an in-memory queue or claim to run the supervisor.
type installedRedisQueue struct {
	client    *redis.Client
	script    string
	keys      []string
	namespace string
	request   Request
	granted   int64
}

func newInstalledRedisQueue(t *testing.T, request Request) *installedRedisQueue {
	t.Helper()
	raw := os.Getenv("JOBSEEK_B0_EXECUTOR_TEST_REDIS_URL")
	options, err := redis.ParseURL(raw)
	if err != nil || options.Addr != "127.0.0.1:6384" || options.DB != 15 || options.Username != "" || options.Password != "" || options.TLSConfig != nil {
		t.Fatal("installed queue requires the dedicated loopback Redis database 15 fixture")
	}
	client := redis.NewClient(options)
	t.Cleanup(func() { _ = client.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	count, err := client.DBSize(ctx).Result()
	if err != nil || count != 0 {
		t.Fatal("installed Redis fixture must be empty")
	}
	rawLua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	digest := sha256.Sum256(rawLua)
	if err != nil || hex.EncodeToString(digest[:]) != "60bc7169651d3e8cc7abfcff6dec799170fa298539904a78b7f865534a3a2803" {
		t.Fatal("reviewed queue Lua identity changed")
	}
	namespace := "installed-" + fixtureID(t)
	tag := "lightpanda-b0:{" + namespace + "}"
	q := &installedRedisQueue{client: client, script: string(rawLua), namespace: namespace, request: request}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		q.keys = append(q.keys, tag+":"+suffix)
	}
	t.Cleanup(func() {
		cleanup, done := context.WithTimeout(context.Background(), 5*time.Second)
		defer done()
		// Delete only the owned namespace and this fixture's legacy keys.
		owned := append([]string(nil), q.keys...)
		owned = append(owned, "scrape:"+request.Task.Envelope.TaskID, "ratelimit:"+request.Task.Envelope.Domain, "scrapes_browser:"+request.Task.Envelope.Domain)
		_ = client.ZRem(cleanup, "ready:browser:2", request.Task.Envelope.Domain).Err()
		_ = client.ZRem(cleanup, "ready:rotation:browser", request.Task.Envelope.Domain).Err()
		if namespace, err := client.HGet(cleanup, "lightpanda-b0:producer-owner", "namespace").Result(); err == nil && namespace == q.namespace {
			owned = append(owned, "lightpanda-b0:producer-owner")
		}
		if err := client.Del(cleanup, owned...).Err(); err != nil {
			t.Error("owned Redis fixture cleanup failed")
		}
		if err := client.HDel(cleanup, "lightpanda-b0:legacy-guard", request.Task.Envelope.TaskID).Err(); err != nil {
			t.Error("owned legacy guard cleanup failed")
		}

	})
	q.accept(t, "initialize_producer", request, 0, 0, 0, "")
	e := request.Task.Envelope
	config, err := json.Marshal(map[string]string{"board_id": e.BoardID, "source_url": e.SourceURL, "domain": e.Domain, "scrape_step": "0", "scrape_interval_hours": "24", "description_r2_hash": ""})
	if err != nil {
		t.Fatal("legacy fixture configuration invalid")
	}
	if err := client.HSet(ctx, "scrape:"+e.TaskID, map[string]string{"board_id": e.BoardID, "source_url": e.SourceURL, "domain": e.Domain, "scrape_step": "0", "scrape_interval_hours": "24", "description_r2_hash": ""}).Err(); err != nil {
		t.Fatal("legacy fixture config unavailable")
	}
	if err := client.ZAdd(ctx, "scrapes_browser:"+e.Domain, redis.Z{Score: float64(e.InitialReadyAtMS) / 1000, Member: e.TaskID}).Err(); err != nil {
		t.Fatal("legacy fixture schedule unavailable")
	}
	if err := client.ZAdd(ctx, "ready:browser:2", redis.Z{Score: float64(e.InitialReadyAtMS) / 1000, Member: e.Domain}).Err(); err != nil {
		t.Fatal("legacy fixture domain schedule unavailable")
	}
	q.accept(t, "activate_legacy", request, 0, e.InitialReadyAtMS, 0, string(config))
	return q
}

func (q *installedRedisQueue) call(t *testing.T, op string, request Request, ttl, ready, expected int64, config string) []string {
	t.Helper()
	task := request.Task
	args := []any{op, task.Envelope.ShardID, strconv.FormatInt(task.Envelope.RoutingEpoch, 10), "go", task.Envelope.TaskID,
		strconv.FormatInt(task.Envelope.ConfigRevision, 10), request.ClaimToken, strconv.FormatInt(ttl, 10), strconv.FormatInt(ready, 10), "3",
		request.TaskPayload, request.PayloadSHA256, task.PayloadSHA1, "64", "0", "", strconv.FormatInt(expected, 10), q.namespace, config, "1", "c1", "1", "0", "browser-use-careers"}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	raw, err := q.client.Eval(ctx, q.script, q.keys, args...).Result()
	if err != nil {
		t.Fatal("isolated Redis transition failed")
	}
	list, ok := raw.([]any)
	if !ok || len(list) != 12 {
		t.Fatal("Redis transition shape differs")
	}
	out := make([]string, len(list))
	for i, value := range list {
		var ok bool
		out[i], ok = value.(string)
		if !ok {
			t.Fatal("Redis transition value differs")
		}
	}
	return out
}
func (q *installedRedisQueue) accept(t *testing.T, op string, r Request, ttl, ready, expected int64, config string) []string {
	t.Helper()
	result := q.call(t, op, r, ttl, ready, expected, config)
	if result[0] != "accepted" {
		t.Fatalf("isolated %s rejected: %s/%s", op, result[0], result[1])
	}
	return result
}
func (q *installedRedisQueue) claim(t *testing.T, ttl int64) Request {
	t.Helper()
	reply := q.accept(t, "claim_next", q.request, ttl, 0, 0, "")
	if reply[3] != q.request.Task.Envelope.TaskID || reply[7] != q.request.PayloadSHA256 || reply[8] != q.request.TaskPayload {
		t.Fatal("Redis claim changed canonical task identity")
	}
	request := q.request
	request.ClaimToken = reply[4]
	value, err := strconv.ParseInt(reply[5], 10, 64)
	if err != nil || value <= 0 || !strings.HasPrefix(request.ClaimToken, strconv.FormatInt(request.Task.Envelope.RoutingEpoch, 10)+":") {
		t.Fatal("Redis claim authority invalid")
	}
	request.LeaseUntilMS = value
	q.request = request
	return request
}
func (q *installedRedisQueue) authorize(t *testing.T, request Request) int64 {
	t.Helper()
	time.Sleep(3 * time.Millisecond)
	reply := q.accept(t, "heartbeat", request, 30000, 0, request.LeaseUntilMS, "")
	value, err := strconv.ParseInt(reply[5], 10, 64)
	if err != nil || value <= request.LeaseUntilMS {
		t.Fatal("Redis authorization did not advance")
	}
	q.granted = value
	return value
}
func (q *installedRedisQueue) census(t *testing.T, ready, inflight, failures int64) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	counts := []struct {
		key     string
		command string
		want    int64
	}{{q.keys[1], "h", 1}, {q.keys[2], "z", ready}, {q.keys[3], "z", inflight}, {q.keys[4], "s", 0}, {q.keys[5], "s", 0}, {q.keys[6], "h", inflight}}
	for _, row := range counts {
		var got int64
		var err error
		switch row.command {
		case "h":
			got, err = q.client.HLen(ctx, row.key).Result()
		case "z":
			got, err = q.client.ZCard(ctx, row.key).Result()
		case "s":
			got, err = q.client.SCard(ctx, row.key).Result()
		}
		if err != nil || got != row.want {
			t.Fatal("Redis queue conservation differs")
		}
	}
	body, err := q.client.HGet(ctx, q.keys[1], q.request.Task.Envelope.TaskID).Result()
	var record struct {
		Failures   int64
		State      string
		ClaimToken *string `json:"claim_token"`
	}
	if err != nil || json.Unmarshal([]byte(body), &record) != nil || record.Failures != failures {
		t.Fatal("Redis failure record differs")
	}
	if inflight == 1 && (record.State != "inflight" || record.ClaimToken == nil || *record.ClaimToken != q.request.ClaimToken) {
		t.Fatal("Redis inflight claim differs")
	}
	if ready == 1 && (record.State != "ready" || record.ClaimToken != nil) {
		t.Fatal("Redis ready record differs")
	}
}

// Local real Redis proof for the reviewed ABI, expiry and stale settlement.
// The separate installed Linux case adds durable PostgreSQL commit and SIGKILL.
func TestPrivateRedisAuthorityExpiryAndConservation(t *testing.T) {
	if os.Getenv("JOBSEEK_B0_EXECUTOR_TEST_REDIS_URL") == "" {
		t.Skip("dedicated Redis fixture not configured")
	}
	_, request := executorFixture(t)
	q := newInstalledRedisQueue(t, request)
	stale := q.claim(t, 100)
	q.census(t, 0, 1, 0)
	time.Sleep(150 * time.Millisecond)
	denied := q.call(t, "heartbeat", stale, 30000, 0, stale.LeaseUntilMS, "")
	if denied[0] == "accepted" || denied[1] != "lease_expired" {
		t.Fatal("expired Redis authorization accepted")
	}
	q.accept(t, "reap_expired", stale, 0, 0, 0, "")
	q.census(t, 1, 0, 1)
	current := q.claim(t, 30000)
	if current.ClaimToken == stale.ClaimToken {
		t.Fatal("recovery reused claim token")
	}
	denied = q.call(t, "complete", stale, 0, 0, stale.LeaseUntilMS, "")
	if denied[0] == "accepted" {
		t.Fatal("stale terminal accepted")
	}
	granted := q.authorize(t, current)
	next := time.Now().Add(time.Hour).UnixMilli()
	q.accept(t, "reschedule_at", current, 0, next, granted, "")
	q.census(t, 1, 0, 0)
	score, err := q.client.ZScore(context.Background(), q.keys[2], current.Task.Envelope.TaskID).Result()
	if err != nil || score != float64(next) {
		t.Fatal("authoritative schedule changed")
	}
}
