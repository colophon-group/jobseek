//go:build integration

package worker

import (
	"bytes"
	"context"
	"crypto/sha1"
	"debug/buildinfo"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/redis/go-redis/v9"
)

type hostForwardFixturePosting struct {
	ID      string `json:"id"`
	First   bool   `json:"first_time"`
	Score   string `json:"legacy_score"`
	ReadyMS int64  `json:"ready_ms"`
}

type hostForwardBuildIdentityOutput struct{ bytes.Buffer }

func (w *hostForwardBuildIdentityOutput) Write(body []byte) (int, error) {
	if len(body) > 4096-w.Len() {
		return 0, io.ErrShortBuffer
	}
	return w.Buffer.Write(body)
}

type hostForwardFixtureSeed struct {
	Board    string                      `json:"board_id"`
	Postings []hostForwardFixturePosting `json:"postings"`
	Pruned   string                      `json:"pruned_posting_id"`
	Terminal string                      `json:"terminal_posting_id"`
}

func seedHostForwardFixture(t *testing.T, ctx context.Context, f nativePipelineFixture) *hostForwardFixtureSeed {
	t.Helper()
	s := &hostForwardFixtureSeed{}
	if f.pg.QueryRow(ctx, "SELECT id::text FROM job_board WHERE company_id=$1::uuid AND board_slug='browser-use-careers'", f.company).Scan(&s.Board) != nil {
		t.Fatal("owned forward board")
	}
	const metadata = `{"scraper_type":"json-ld","scraper_config":{"browser_backend":"lightpanda","render":true,"routing_revision":"go-b0-1","timeout":5000,"wait":"load","wait_fallback":null}}`
	if _, err := f.pg.Exec(ctx, "UPDATE job_board SET metadata=$1::jsonb WHERE id=$2::uuid", metadata, s.Board); err != nil || f.r.HSet(ctx, "board:"+s.Board, "metadata", metadata).Err() != nil {
		t.Fatal("matching owned parser assignment")
	}
	s.Postings = []hostForwardFixturePosting{{fixtureID(t), true, "350.0001", 350001}, {fixtureID(t), false, "1925089445.123001", 1925089445124}}
	s.Pruned = fixtureID(t)
	s.Terminal = fixtureID(t)
	for _, id := range []string{s.Postings[0].ID, s.Postings[1].ID, s.Pruned, s.Terminal} {
		url := "https://jobs.example.test/é/" + id
		if _, err := f.pg.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,description_r2_hash,next_scrape_at) VALUES($1::uuid,$2::uuid,$3::uuid,$4,-9223372036854775808,'2031-01-02 03:04:05.100001+00')`, id, f.company, s.Board, url); err != nil {
			t.Fatal("owned forward posting")
		}
		t.Cleanup(func() { _, _ = f.pg.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", id) })
	}
	if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", s.Terminal); err != nil {
		t.Fatal("owned inactive bootstrap posting")
	}
	config := hostForwardBootstrapConfig(s)
	if f.r.HSet(ctx, "scrape:"+s.Terminal, config).Err() != nil || f.r.ZAdd(ctx, "ft_scrapes_browser:jobs.example.test", redis.Z{Member: s.Terminal, Score: 0}).Err() != nil {
		t.Fatal("owned bootstrap legacy schedule")
	}
	for _, p := range s.Postings {
		config := map[string]string{"domain": "jobs.example.test", "board_id": s.Board, "source_url": "https://jobs.example.test/é/" + p.ID, "description_r2_hash": "7", "scrape_step": "0"}
		prefix := "scrapes_browser:"
		if p.First {
			prefix = "ft_scrapes_browser:"
		}
		score, err := strconv.ParseFloat(p.Score, 64)
		if err != nil || f.r.HSet(ctx, "scrape:"+p.ID, config).Err() != nil || f.r.ZAdd(ctx, prefix+"jobs.example.test", redis.Z{Member: p.ID, Score: score}).Err() != nil {
			t.Fatal("owned exact legacy schedule")
		}
	}
	// Shared readiness indexes also contain domains outside this transfer.
	// Keep them populated so conservation proves member scores and key expiry.
	for n, key := range hostForwardReadinessKeys() {
		if f.r.ZAdd(ctx, key, redis.Z{Member: "unrelated.example.test", Score: float64(n) + 0.125}).Err() != nil {
			t.Fatal("owned unrelated readiness seed")
		}
	}
	return s
}

func hostForwardBootstrapConfig(s *hostForwardFixtureSeed) map[string]string {
	return map[string]string{"domain": "jobs.example.test", "board_id": s.Board, "source_url": "https://jobs.example.test/é/" + s.Terminal, "description_r2_hash": "7", "scrape_step": "0"}
}

// Opening the producer socket grants no queue authority. Bootstrap through its
// actual preparation/activation path, then complete one owned inactive posting
// using the reviewed lifecycle script. It remains terminal lifetime work while
// the two active legacy postings enter the protected forward journal.
func initializeHostForwardFixtureTerminal(t *testing.T, ctx context.Context, f nativePipelineFixture, seed *hostForwardFixtureSeed, epoch int64, lua []byte) map[string]any {
	t.Helper()
	control := b0producer.NewClient()
	task := b0producer.Task{PostingID: seed.Terminal, Domain: "jobs.example.test", NextScrapeAtMS: 0, Config: hostForwardBootstrapConfig(seed), Browser: true, FirstTime: true, LegacyScheduleScore: "0"}
	prepared, err := control.Prepare(ctx, task)
	if err != nil || prepared.Outcome != "prepared" {
		t.Fatal("actual native bootstrap preparation", err)
	}
	activated, err := control.Activate(ctx, task, prepared.PreparationDigest)
	if err != nil || activated.Outcome != "activated" || !activated.Activated || activated.PayloadSHA256 != prepared.PayloadSHA256 {
		t.Fatal("actual native bootstrap activation", err)
	}
	keys := []string{}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		keys = append(keys, "lightpanda-b0:{host-selected-redis}:"+suffix)
	}
	args := []any{"claim_next", "lightpanda-b0", strconv.FormatInt(epoch, 10), "go", "", "0", strconv.FormatInt(time.Now().UnixMilli(), 10), "600000", "0", "0", "", "", "", "64", "2.0", "", "0", "host-selected-redis", "", "0", "c1", 1, "0", "browser-use-careers"}
	claim, err := f.r.Eval(ctx, string(lua), keys, args...).Slice()
	if err != nil || len(claim) != 12 || claim[0] != "accepted" || claim[3] != seed.Terminal || claim[7] != prepared.PayloadSHA256 {
		t.Fatal("owned activated bootstrap claim", err)
	}
	payload, ok := claim[8].(string)
	if !ok || hostDigest([]byte(payload)) != prepared.PayloadSHA256 {
		t.Fatal("owned bootstrap task bytes")
	}
	hash := sha1.Sum([]byte(payload))
	args[0], args[4], args[5], args[6], args[11], args[12], args[16] = "complete", claim[3], claim[6], claim[4], claim[7], hex.EncodeToString(hash[:]), claim[5]
	if reply, err := f.r.Eval(ctx, string(lua), keys, args...).Slice(); err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("owned bootstrap completion", err)
	}
	var active bool
	if f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", seed.Terminal).Scan(&active) != nil || active {
		t.Fatal("bootstrap posting must remain canonically inactive")
	}
	// The fixture owns this origin and has no other claimant. Remove its short
	// bootstrap throttle before capturing the permanent forward source snapshot.
	if f.r.HLen(ctx, keys[6]).Val() != 0 || f.r.ZCard(ctx, keys[3]).Val() != 0 || f.r.Del(ctx, "ratelimit:jobs.example.test").Err() != nil {
		t.Fatal("owned bootstrap origin release")
	}
	record, err := f.r.HGet(ctx, keys[1], seed.Terminal).Result()
	guard, e := f.r.HGet(ctx, "lightpanda-b0:legacy-guard", seed.Terminal).Result()
	if err != nil || e != nil || !f.r.SIsMember(ctx, keys[5], seed.Terminal).Val() {
		t.Fatal("owned terminal bootstrap retention")
	}
	return map[string]any{"posting_id": seed.Terminal, "preparation_sha256": prepared.PreparationDigest, "payload_sha256": prepared.PayloadSHA256, "terminal_record_json": record, "terminal_record_sha256": hostDigest([]byte(record)), "terminal_guard": guard, "actual_native_activation_and_reviewed_Lua_completion": true, "inactive_SQL_posting_and_canonical_rows_unchanged": true}
}

func startHostForwardInstalledProducer(t *testing.T, ctx context.Context, source, image, arch, selectedURL string, epoch int64, lua []byte) (map[string]any, func()) {
	t.Helper()
	if runtime.GOOS != "linux" || os.Geteuid() != 0 || os.Getenv("GITHUB_ACTIONS") != "true" || os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_HOST_SELECTED_REDIS") != "1" {
		t.Fatal("owned producer requires disposable root harness")
	}
	binaryPath, proofPath := os.Getenv("JOBSEEK_HOST_FORWARD_PRODUCER_BINARY"), os.Getenv("JOBSEEK_HOST_FORWARD_PRODUCER_IMAGE_PROOF")
	var proof struct {
		Source string `json:"source_revision"`
		Image  string `json:"image_id"`
		Arch   string `json:"architecture"`
		Binary string `json:"producer_binary_sha256"`
	}
	proofInfo, err := os.Stat(proofPath)
	if err != nil || !proofInfo.Mode().IsRegular() || proofInfo.Size() > 1<<20 {
		t.Fatal("bounded producer image proof")
	}
	body, err := os.ReadFile(proofPath)
	if err != nil || json.Unmarshal(body, &proof) != nil || proof.Source != source || proof.Image != image || proof.Arch != arch || !planPattern.MatchString(proof.Binary) || !filepath.IsAbs(binaryPath) {
		t.Fatal("independent producer image/source binding")
	}
	before, err := os.Lstat(binaryPath)
	if err != nil || !before.Mode().IsRegular() || before.Size() < 1 || before.Size() > 64<<20 {
		t.Fatal("bounded extracted producer binary")
	}
	binary, err := os.ReadFile(binaryPath)
	after, e := os.Lstat(binaryPath)
	if err != nil || e != nil || !os.SameFile(before, after) || !before.ModTime().Equal(after.ModTime()) || hostDigest(binary) != proof.Binary {
		t.Fatal("extracted producer byte identity")
	}
	info, err := buildinfo.ReadFile(binaryPath)
	if err != nil || info.Path != "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-supervisor" || info.GoVersion != "go1.26.4" {
		t.Fatal("producer compiled identity")
	}
	var compiledArch string
	for _, setting := range info.Settings {
		if setting.Key == "GOARCH" {
			compiledArch = setting.Value
		}
	}
	if compiledArch != arch {
		t.Fatal("producer compiled architecture")
	}
	parent := filepath.Dir(b0producer.SocketPath)
	if os.Mkdir(parent, 0700) != nil {
		t.Fatal("owned producer refuses existing fixed socket directory")
	}
	owned, err := os.Lstat(parent)
	if err != nil {
		t.Fatal("owned producer directory")
	}
	t.Cleanup(func() {
		current, err := os.Lstat(parent)
		if err != nil || !os.SameFile(owned, current) {
			t.Error("owned producer directory substituted")
			return
		}
		if os.RemoveAll(parent) != nil {
			t.Error("owned producer cleanup")
		}
	})
	if os.Chown(parent, 10001, 10001) != nil {
		t.Fatal("owned producer directory identity")
	}
	write := func(name string, body []byte, mode os.FileMode) string {
		path := filepath.Join(parent, name)
		if os.WriteFile(path, body, mode) != nil || os.Chown(path, 10001, 10001) != nil {
			t.Fatal("owned producer executable/input")
		}
		return path
	}
	executable := write("installed-producer", binary, 0500)
	identityCtx, identityCancel := context.WithTimeout(ctx, 5*time.Second)
	defer identityCancel()
	identityCommand := exec.CommandContext(identityCtx, executable, "--build-info")
	identityCommand.Env = []string{}
	identityCommand.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 10001, Gid: 10001, Groups: []uint32{}}}
	var identityOutput hostForwardBuildIdentityOutput
	identityCommand.Stdout = &identityOutput
	if identityCommand.Run() != nil {
		t.Fatal("installed producer read-only build identity")
	}
	var identity struct {
		Schema    string `json:"schema"`
		Source    string `json:"source_revision"`
		OS        string `json:"goos"`
		Arch      string `json:"goarch"`
		GoVersion string `json:"go_version"`
	}
	decoder := json.NewDecoder(bytes.NewReader(identityOutput.Bytes()))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&identity) != nil || decoder.Decode(new(any)) != io.EOF || identity.Schema != "jobseek.lightpanda-b0.build-identity/v1" || identity.Source != source || identity.OS != "linux" || identity.Arch != arch || identity.GoVersion != info.GoVersion {
		t.Fatal("producer linked source/architecture identity")
	}
	luaPath := write("reviewed-queue.lua", lua, 0400)
	log, err := os.OpenFile(filepath.Join(parent, "producer.log"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal("private producer log")
	}
	command := exec.CommandContext(ctx, executable, "producer")
	command.Env = []string{"LIGHTPANDA_B0_PRODUCER_MODE=enabled", "LIGHTPANDA_B0_PRODUCER_COHORT=c1", "LIGHTPANDA_B0_PRODUCER_CLIENT_UID=0", "LIGHTPANDA_B0_PRODUCER_SOCKET=" + b0producer.SocketPath, "LIGHTPANDA_B0_QUEUE_NAMESPACE=host-selected-redis", "LIGHTPANDA_B0_SHARD_ID=lightpanda-b0", "LIGHTPANDA_B0_ROUTING_EPOCH=" + strconv.FormatInt(epoch, 10), "LIGHTPANDA_B0_LUA_PATH=" + luaPath, "REDIS_URL=" + selectedURL, "LIGHTPANDA_B0_ROLLBACK_PLAN_DIGEST=" + strings.Repeat("a", 64), "LIGHTPANDA_B0_SOURCE_RECEIPT_SHA256=" + strings.Repeat("b", 64)}
	command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 10001, Gid: 10001, Groups: []uint32{}}}
	command.Stdout, command.Stderr = log, log
	if command.Start() != nil {
		_ = log.Close()
		t.Fatal("owned installed producer start")
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	var once sync.Once
	stop := func() { once.Do(func() { _ = command.Process.Kill(); <-done; _ = log.Close() }) }
	t.Cleanup(stop)
	control := b0producer.NewClient()
	deadline := time.Now().Add(10 * time.Second)
	for {
		if _, err := control.Manifest(ctx, "c1"); err == nil {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("owned native producer context")
		default:
		}
		if time.Now().After(deadline) {
			t.Fatal("actual fixed native producer readiness")
		}
		time.Sleep(25 * time.Millisecond)
	}
	pid := command.Process.Pid
	status, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/status")
	if err != nil {
		t.Fatal("actual producer process identity")
	}
	var uid, gid, groups bool
	for _, line := range strings.Split(string(status), "\n") {
		words := strings.Fields(line)
		if len(words) == 5 && (words[0] == "Uid:" || words[0] == "Gid:") {
			valid := reflect.DeepEqual(words[1:], []string{"10001", "10001", "10001", "10001"})
			if words[0] == "Uid:" {
				uid = valid
			} else {
				gid = valid
			}
		}
		if len(words) == 1 && words[0] == "Groups:" {
			groups = true
		}
	}
	exe, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/exe")
	socket, e := os.Lstat(b0producer.SocketPath)
	if err != nil || e != nil || !uid || !gid || !groups || hostDigest(exe) != proof.Binary || socket.Mode() != os.ModeSocket|0600 {
		t.Fatal("actual executable/credentials/socket identity")
	}
	stat, ok := socket.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != 10001 || stat.Gid != 10001 || stat.Nlink != 1 || stat.Ino == 0 {
		t.Fatal("actual producer socket inode")
	}
	return map[string]any{"source_revision": source, "image_id": image, "architecture": arch, "binary_sha256": proof.Binary, "go_version": info.GoVersion, "build_identity_json": identityOutput.String(), "build_identity_sha256": hostDigest(identityOutput.Bytes()), "pid": pid, "uid": 10001, "gid": 10001, "supplementary_groups": []int{}, "socket_path": b0producer.SocketPath, "socket_device": uint64(stat.Dev), "socket_inode": stat.Ino, "authenticated_fixed_peer_manifest": true, "routing_epoch": epoch, "namespace": "host-selected-redis"}, stop
}

func validateHostForwardSeedPlan(t *testing.T, seed *hostForwardFixtureSeed, raw json.RawMessage) {
	t.Helper()
	var plan struct {
		Tasks []struct {
			ID     string            `json:"posting_id"`
			First  bool              `json:"first_time"`
			Score  string            `json:"legacy_schedule_score"`
			Ready  int64             `json:"next_scrape_at_ms"`
			Config map[string]string `json:"config"`
			PGHash string            `json:"postgres_description_r2_hash"`
		} `json:"tasks"`
		Pruned    []string `json:"unqueued_posting_ids"`
		Terminal  []string `json:"retained_terminal_ids"`
		Occupancy int64    `json:"lifetime_occupancy"`
		New       int64    `json:"new_record_count"`
		Projected int64    `json:"projected_lifetime_occupancy"`
	}
	if json.Unmarshal(raw, &plan) != nil || len(plan.Tasks) != 2 || !reflect.DeepEqual(plan.Pruned, []string{seed.Pruned}) || !reflect.DeepEqual(plan.Terminal, []string{seed.Terminal}) || plan.Occupancy != 1 || plan.New != 2 || plan.Projected != 3 {
		t.Fatal("approved forward manifest cardinality/pruned work")
	}
	for _, expected := range seed.Postings {
		found := false
		for _, task := range plan.Tasks {
			if task.ID != expected.ID {
				continue
			}
			found = true
			if task.First != expected.First || task.Score != expected.Score || task.Ready != expected.ReadyMS || task.PGHash != "-9223372036854775808" || task.Config["description_r2_hash"] != "7" || task.Config["scrape_interval_hours"] != "24" || task.Config["board_id"] != seed.Board {
				t.Fatal("approved exact schedule/hint/truth")
			}
		}
		if !found {
			t.Fatal("approved transfer lost posting")
		}
	}
}

func verifyHostForwardTransferredQueue(t *testing.T, ctx context.Context, f nativePipelineFixture, seed *hostForwardFixtureSeed, raw json.RawMessage, epoch int64) {
	t.Helper()
	var plan struct {
		Tasks []struct {
			ID          string `json:"posting_id"`
			SHA         string `json:"payload_sha256"`
			Preparation string `json:"preparation_digest"`
		} `json:"tasks"`
		Snapshot struct {
			B0 struct {
				Records map[string]string `json:"records"`
				Guards  map[string]string `json:"guards"`
			} `json:"b0"`
		} `json:"snapshot"`
	}
	if json.Unmarshal(raw, &plan) != nil {
		t.Fatal("approved retained forward tasks")
	}
	base := "lightpanda-b0:{host-selected-redis}:"
	records, err := f.r.HGetAll(ctx, base+"records").Result()
	if err != nil || len(records) != 3 {
		t.Fatal("transferred task record conservation", err)
	}
	if records[seed.Terminal] == "" || records[seed.Terminal] != plan.Snapshot.B0.Records[seed.Terminal] || f.r.HGet(ctx, "lightpanda-b0:legacy-guard", seed.Terminal).Val() != plan.Snapshot.B0.Guards[seed.Terminal] || !f.r.SIsMember(ctx, base+"terminal", seed.Terminal).Val() {
		t.Fatal("forward transfer changed retained terminal authority")
	}
	for _, count := range []struct {
		name    string
		command *redis.IntCmd
		want    int64
	}{
		{"ready", f.r.ZCard(ctx, base+"ready"), 2},
		{"inflight", f.r.ZCard(ctx, base+"inflight"), 0},
		{"legacy guard", f.r.HLen(ctx, "lightpanda-b0:legacy-guard"), 3},
		{"dead", f.r.SCard(ctx, base+"dead"), 0},
		{"terminal", f.r.SCard(ctx, base+"terminal"), 1},
		{"origin holders", f.r.HLen(ctx, base+"origin-holders"), 0},
	} {
		got, err := count.command.Result()
		if err != nil || got != count.want {
			t.Fatal("transferred task conservation", count.name, err)
		}
	}
	for _, p := range seed.Postings {
		var record struct {
			ID          string `json:"task_id"`
			State       string `json:"state"`
			Payload     string `json:"payload"`
			SHA         string `json:"payload_sha256"`
			Preparation string `json:"preparation_digest"`
		}
		if json.Unmarshal([]byte(records[p.ID]), &record) != nil || record.ID != p.ID || record.State != "ready" {
			t.Fatal("actual ready native record")
		}
		task, err := b0task.Decode(record.Payload, record.SHA, b0task.Route{ShardID: "lightpanda-b0", RoutingEpoch: epoch, EngineOwner: "go"})
		if err != nil || task.Envelope.TaskID != p.ID || task.Envelope.BoardID != seed.Board || task.Envelope.InitialReadyAtMS != p.ReadyMS {
			t.Fatal("actual canonical task payload/schedule")
		}
		var approved bool
		for _, a := range plan.Tasks {
			if a.ID == p.ID && a.SHA == record.SHA {
				approved = true
			}
		}
		if !approved {
			t.Fatal("actual transferred payload differs from approval")
		}
		ready, err := f.r.ZScore(ctx, base+"ready", p.ID).Result()
		if err != nil || ready != float64(p.ReadyMS) {
			t.Fatal("actual ready index schedule differs from approval")
		}
		for _, prefix := range []string{"ft_scrapes_browser:", "scrapes_browser:"} {
			if _, err := f.r.ZScore(ctx, prefix+"jobs.example.test", p.ID).Result(); err != redis.Nil {
				t.Fatal("transferred posting retained legacy membership")
			}
		}
		guard, err := f.r.HGet(ctx, "lightpanda-b0:legacy-guard", p.ID).Result()
		if err != nil || !strings.Contains(guard, "|"+seed.Board+"|jobs.example.test|") || !strings.HasSuffix(guard, "|"+p.Score) {
			t.Fatal("exact legacy guard schedule")
		}
	}
	if _, ok := records[seed.Pruned]; ok {
		t.Fatal("pruned work recreated")
	}
	var phase, planSHA string
	if f.pg.QueryRow(ctx, "SELECT phase,reserved_plan_sha256 FROM crawler_ownership_transition WHERE source_revision=$1 AND routing_epoch=$2", ordinaryFixtureSourceRevision(t), epoch).Scan(&phase, &planSHA) != nil || phase != "active" || !planPattern.MatchString(planSHA) {
		t.Fatal("actual ordinary publication not active")
	}
	projection, err := f.r.Get(ctx, "ordinary:ownership:active").Result()
	if err != nil || hostDigest([]byte(projection)) != planSHA {
		t.Fatal("actual published ordinary projection")
	}
}

func verifyHostForwardProducerStillOwned(t *testing.T, proof map[string]any) {
	t.Helper()
	pid, ok := proof["pid"].(int)
	if !ok || pid < 1 {
		t.Fatal("owned producer PID proof")
	}
	body, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/exe")
	socket, e := os.Lstat(b0producer.SocketPath)
	if err != nil || e != nil || hostDigest(body) != proof["binary_sha256"] || socket.Mode() != os.ModeSocket|0600 {
		t.Fatal("producer changed before final retained readback")
	}
	stat, ok := socket.Sys().(*syscall.Stat_t)
	if !ok || stat.Ino != proof["socket_inode"] || uint64(stat.Dev) != proof["socket_device"] || stat.Uid != 10001 || stat.Gid != 10001 {
		t.Fatal("producer socket changed across journal")
	}
	proof["same_process_executable_and_socket_after_exact_retry"] = true
}

func assertHostForwardUnrelatedValues(t *testing.T, before, after map[string]string, seed *hostForwardFixtureSeed) {
	t.Helper()
	assertHostForwardReadiness(t, before, after, nil)
	allowed := func(key string) bool {
		for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
			if key == "lightpanda-b0:{host-selected-redis}:"+suffix {
				return true
			}
		}
		if key == "lightpanda-b0:legacy-guard" || key == "ordinary:ownership:active" || key == "crawler:ownership:transition" || key == "ft_scrapes_browser:jobs.example.test" || key == "scrapes_browser:jobs.example.test" {
			return true
		}
		for _, p := range seed.Postings {
			if key == "scrape:"+p.ID {
				return true
			}
		}
		return false
	}
	for key, value := range before {
		if hostForwardReadinessKey(key) {
			continue // Member-level conservation is checked above.
		}
		if !allowed(key) && after[key] != value {
			t.Fatal("unrelated prior Redis value changed", key)
		}
	}
	for key, value := range after {
		if hostForwardReadinessKey(key) {
			continue
		}
		if !allowed(key) && before[key] != value {
			t.Fatal("unrelated new Redis value added", key)
		}
	}
}

func hostForwardReadinessKeys() []string {
	keys := []string{}
	for _, kind := range []string{"simple", "browser"} {
		keys = append(keys, "ready:rotation:"+kind)
		for tier := 0; tier < 3; tier++ {
			keys = append(keys, fmt.Sprintf("ready:%s:%d", kind, tier))
		}
	}
	return keys
}

func hostForwardReadinessKey(key string) bool {
	for _, candidate := range hostForwardReadinessKeys() {
		if key == candidate {
			return true
		}
	}
	return false
}

// Read the same logical snapshot format as fullColdExecutableRedisSnapshot.
// Only the owned domain may change; unrelated members, scores and expiry stay
// exact. An absent key and a key containing only the owned domain project empty.
func hostForwardReadinessProjection(raw string) (string, *float64, error) {
	if raw == "" {
		return "", nil, nil
	}
	parts := strings.SplitN(raw, ":", 3)
	if len(parts) != 3 || parts[0] != "zset" || (parts[1] != "true" && parts[1] != "false") {
		return "", nil, fmt.Errorf("unexpected readiness type/expiry")
	}
	var rows []struct {
		Score  float64
		Member []byte
	}
	if err := json.Unmarshal([]byte(parts[2]), &rows); err != nil {
		return "", nil, err
	}
	var owned *float64
	unrelated := rows[:0]
	for _, row := range rows {
		if string(row.Member) == "jobs.example.test" {
			if owned != nil {
				return "", nil, fmt.Errorf("duplicate owned readiness member")
			}
			score := row.Score
			owned = &score
		} else {
			unrelated = append(unrelated, row)
		}
	}
	if len(unrelated) == 0 {
		return "", owned, nil
	}
	encoded, err := json.Marshal(unrelated)
	return parts[0] + ":" + parts[1] + ":" + string(encoded), owned, err
}

func assertHostForwardReadiness(t *testing.T, before, after map[string]string, restored *hostForwardFixtureSeed) {
	t.Helper()
	for _, key := range hostForwardReadinessKeys() {
		old, _, err := hostForwardReadinessProjection(before[key])
		current, owned, currentErr := hostForwardReadinessProjection(after[key])
		if err != nil || currentErr != nil || old != current {
			t.Fatal("unrelated readiness member/score/type/expiry changed", key, err, currentErr)
		}
		if restored != nil && key == "ready:browser:0" {
			want, err := strconv.ParseFloat(restored.Postings[0].Score, 64)
			if err != nil || owned == nil || *owned != want {
				t.Fatal("restored first-time readiness score", key)
			}
		} else if owned != nil {
			t.Fatal("unexpected owned readiness membership", key)
		}
	}
}
