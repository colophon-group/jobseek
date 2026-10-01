package worker

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	greenhouse "github.com/colophon-group/jobseek/apps/crawler/go/greenhouse-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func fixtureID(t *testing.T) string {
	t.Helper()
	var bytes [16]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		t.Fatal(err)
	}
	return fmt.Sprintf("%x-%x-%x-%x-%x", bytes[:4], bytes[4:6], bytes[6:8], bytes[8:10], bytes[10:])
}

type nativePipelineFixture struct {
	a                             *queue.Authority
	client                        *queue.Client
	r                             *redis.Client
	pg                            *pgxpool.Pool
	dsn, board, company, original string
}

func privatePipelineFixture(t *testing.T) nativePipelineFixture {
	t.Helper()
	ctx := context.Background()
	dsn := os.Getenv("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("required migrated PostgreSQL fixture unavailable")
		}
		t.Skip("requires migrated private ordinary PostgreSQL fixture")
	}
	parsed, err := url.Parse(dsn)
	if err != nil || !strings.HasSuffix(parsed.Path, "_ordinary_worker_test") || (parsed.Hostname() != "127.0.0.1" && parsed.Hostname() != "localhost") {
		t.Fatal("native pipeline needs a private local database")
	}
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid private fixture database")
	}
	config.MaxConns = 2
	config.MinConns = 0
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:fixture:ordinary-pipeline"
	pg, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pg.Close)
	binary, err := exec.LookPath("redis-server")
	if err != nil {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_REDIS") == "1" {
			t.Fatal("required private Redis unavailable")
		}
		t.Skip("requires private Redis")
	}
	root, err := os.MkdirTemp("/tmp", "jow-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(root) })
	log, err := os.OpenFile(filepath.Join(root, "redis.log"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(root, "redis.sock")
	command := exec.Command(binary, "--port", "0", "--unixsocket", socket, "--unixsocketperm", "700", "--save", "", "--appendonly", "no", "--dir", root)
	command.Env = []string{"PATH=" + os.Getenv("PATH"), "LC_ALL=C", "LANG=C"}
	command.Stdout, command.Stderr = log, log
	if err := command.Start(); err != nil {
		_ = log.Close()
		t.Fatal("private Redis startup failed")
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	t.Cleanup(func() {
		_ = command.Process.Signal(os.Interrupt)
		select {
		case <-done:
		case <-time.After(3 * time.Second):
			_ = command.Process.Kill()
			<-done
		}
		_ = log.Close()
	})
	r := redis.NewClient(&redis.Options{Network: "unix", Addr: socket})
	t.Cleanup(func() { _ = r.Close() })
	ready, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	for r.Ping(ready).Err() != nil {
		if ready.Err() != nil {
			t.Fatal("private Redis readiness expired")
		}
		time.Sleep(10 * time.Millisecond)
	}
	client, err := queue.Open("unix://"+socket, queue.Settings{LeaseTTL: time.Minute, MaxDomains: 10})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Close() })
	f := nativePipelineFixture{client: client, r: r, pg: pg, dsn: dsn, board: fixtureID(t), company: fixtureID(t), original: fixtureID(t)}
	if _, err := pg.Exec(ctx, "INSERT INTO company(id,slug,name) VALUES($1::uuid,$2,'Native pipeline fixture')", f.company, "native-"+f.company); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = pg.Exec(ctx, "DELETE FROM job_posting WHERE company_id=$1::uuid", f.company)
		_, _ = pg.Exec(ctx, "DELETE FROM job_board WHERE company_id=$1::uuid", f.company)
		_, _ = pg.Exec(ctx, "DELETE FROM company WHERE id=$1::uuid", f.company)
	})
	metadata := `{"token":"fixture","scraper_type":"skip","_monitor_config_fingerprint":"fixture"}`
	if _, err := pg.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,throttle_key,check_interval_minutes,scrape_interval_hours,next_check_at)
 VALUES($1::uuid,$2::uuid,$3,'https://job-boards.greenhouse.io/fixture','greenhouse',$4::jsonb,'greenhouse',60,24,now()-interval '1 minute')`, f.board, f.company, "native-"+f.board, metadata); err != nil {
		t.Fatal(err)
	}
	if _, err := pg.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,last_seen_at,next_scrape_at)
 VALUES($1::uuid,$2::uuid,$3::uuid,$4,ARRAY['Original'],ARRAY['en'],now()-interval '1 day',now())`, f.original, f.company, f.board, "https://example.com/old/"+f.original); err != nil {
		t.Fatal(err)
	}
	projection := map[string]string{"board_slug": "native-" + f.board, "board_url": "https://job-boards.greenhouse.io/fixture", "crawler_type": "greenhouse", "company_id": f.company, "domain": "greenhouse", "throttle_key": "greenhouse", "monitor_needs_browser": "0", "scraper_needs_browser": "0", "check_interval_minutes": "60", "scrape_interval_hours": "24", "metadata": metadata}
	if err := r.HSet(ctx, "board:"+f.board, projection).Err(); err != nil {
		t.Fatal(err)
	}
	for key, member := range map[string]string{"monitors_simple:greenhouse": f.board, "ready:simple:1": "greenhouse"} {
		if err := r.ZAdd(ctx, key, redis.Z{Score: 1, Member: member}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	var epoch int64
	if err := pgx.BeginFunc(ctx, pg, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(7544422533504811009)"); err != nil {
			return err
		}
		return tx.QueryRow(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')").Scan(&epoch)
	}); err != nil {
		t.Fatal(err)
	}
	stage, err := queue.OpenAuthority(ctx, dsn, client, epoch)
	if err != nil {
		t.Fatal(err)
	}
	defer stage.Close()
	plan, err := stage.StageGreenhouseOwnership(ctx, ordinaryFixtureSourceRevision(t), []string{f.board})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256()); err != nil {
			t.Error("private plan retirement failed")
		}
	})
	// Only this isolated database fixture activates a staged plan. Production
	// has no activation endpoint here and still requires full quiesced cutover.
	if _, err := pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", plan.SHA256()); err != nil {
		t.Fatal(err)
	}
	var body string
	if err := pg.QueryRow(ctx, "SELECT payload FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.SHA256()).Scan(&body); err != nil {
		t.Fatal(err)
	}
	if err := r.Set(ctx, "ordinary:ownership:active", body, 0).Err(); err != nil {
		t.Fatal(err)
	}
	f.a, err = queue.OpenOwnedAuthority(ctx, dsn, client, epoch, plan.SHA256(), plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(f.a.Close)
	return f
}

func TestRealOwnedPipelineNativePreparationChunksLifecycleAndReceipt(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	nativeDSN := privatePipelineReferenceDSN(t, f)
	store, err := executor.OpenOrdinaryLookupStore(ctx, nativeDSN)
	if err != nil {
		t.Fatal(err)
	}
	locations, err := executor.LoadLocations(ctx, store)
	if err != nil {
		t.Fatal(err)
	}
	defer locations.Close()
	defer store.Close()
	lookups, err := executor.LoadLookups(ctx, store)
	if err != nil {
		t.Fatal(err)
	}
	matcher, err := enrichment.Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	preparer := NativeRichPreparer{&executor.Processor{Matcher: matcher, Lookups: lookups, Locations: locations}}
	claim, err := f.a.Claim(ctx, queue.Simple)
	if err != nil || claim == nil {
		t.Fatal("owned pipeline claim unavailable")
	}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	title, description := "Senior Software Engineer", "<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>"
	input := greenhouse.Inventory{Jobs: make([]greenhouse.Job, 1001)}
	for i := range input.Jobs {
		input.Jobs[i] = greenhouse.Job{URL: fmt.Sprintf("https://job-boards.greenhouse.io/fixture/jobs/%s-%d", f.company, i), Title: &title, Description: &description, Language: "en", Locations: []string{"Zurich"}}
	}
	// Last duplicate content survives global normalization across native chunks.
	last := "Last duplicate title"
	duplicate := input.Jobs[0]
	duplicate.Title = &last
	input.Jobs = append(input.Jobs, duplicate)
	rawJobs := make([]map[string]any, 0, len(input.Jobs))
	for _, job := range input.Jobs {
		rawJobs = append(rawJobs, map[string]any{"absolute_url": job.URL, "title": job.Title, "content": job.Description, "location": map[string]string{"name": "Zurich"}, "language": job.Language})
	}
	payload, err := json.Marshal(map[string]any{"jobs": rawJobs})
	if err != nil {
		t.Fatal(err)
	}
	requests := 0
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		requests++
		if request.URL.Path != "/v1/boards/fixture/jobs" || request.URL.Query().Get("content") != "true" {
			t.Error("owned fixture fetched a different provider resource")
		}
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write(payload)
	}))
	t.Cleanup(server.Close)
	client, transport := directFixtureClient(t, server)
	transport.inner.TLSClientConfig.ServerName = "example.com" // fixture certificate SAN; verification remains enabled
	transport.lookup = func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("8.8.8.8")}, nil
	}
	transport.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != "8.8.8.8:443" {
			t.Error("owned discovery did not pin the validated API address")
		}
		// Only the physical destination changes for this owned fixture. Native
		// DNS guard, TLS verification, request/body accounting and parsing run.
		return (&net.Dialer{}).DialContext(ctx, network, strings.TrimPrefix(server.URL, "https://"))
	}
	now, err := f.r.Time(ctx).Result()
	if err != nil {
		t.Fatal(err)
	}
	if err := f.r.Set(ctx, "host_open:boards-api.greenhouse.io", fmt.Sprintf("%.6f", float64(now.Unix())-1), time.Hour).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.r.Set(ctx, "host_fail:boards-api.greenhouse.io", "2", time.Hour).Err(); err != nil {
		t.Fatal(err)
	}
	result, err := RunGreenhouseClaim(ctx, f.a, claim, &VerifiedDirectHTTP{client: client}, preparer, circuits)
	if err != nil || !result.Settled || requests != 1 || result.Batches.Inserted != 1001 || result.Cycle.Gone != 1 || result.Cycle.Receipt == nil {
		t.Fatalf("native claim runner did not complete the owned assembly: %v", err)
	}
	traffic := result.HTTP
	if traffic.Requests != 1 || traffic.Responses != 1 || traffic.NoResponse != 0 || traffic.EncodedBytes != int64(len(payload)) || traffic.LastHost != "boards-api.greenhouse.io" {
		t.Fatalf("owned discovery lost native origin/body conservation: %+v", traffic)
	}
	var active, descriptions, noDetails int
	if err := f.pg.QueryRow(ctx, "SELECT count(*) FILTER(WHERE is_active),count(*) FILTER(WHERE next_scrape_at IS NULL) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&active, &noDetails); err != nil || active != 1001 || noDetails != 1002 {
		t.Fatal("whole inventory lost posts/absence/no-detail policy")
	}
	if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions d JOIN job_posting jp ON jp.id=d.posting_id WHERE jp.board_id=$1::uuid AND d.html=$2 AND NOT d.r2_uploaded", f.board, description).Scan(&descriptions); err != nil || descriptions != 1001 {
		t.Fatal("native preparation/persistence lost description bytes or pending R2 state")
	}
	var derived, taxonomy int
	if err := f.pg.QueryRow(ctx, "SELECT count(*) FILTER(WHERE location_ids=ARRAY[2]::integer[] AND technology_ids=ARRAY[4]::integer[] AND salary_currency='CHF'),count(*) FILTER(WHERE occupation_id=41 AND seniority_id=7) FROM job_posting WHERE board_id=$1::uuid AND is_active", f.board).Scan(&derived, &taxonomy); err != nil || derived != 1001 || taxonomy != 1000 {
		t.Fatalf("assembled native lookup/location/derivation pipeline mismatch: derived=%d taxonomy=%d error=%v", derived, taxonomy, err)
	}
	var titles []string
	if err := f.pg.QueryRow(ctx, "SELECT titles FROM job_posting WHERE source_url=$1", duplicate.URL).Scan(&titles); err != nil || len(titles) != 1 || titles[0] != last {
		t.Fatal("global duplicate content winner changed")
	}
	if f.r.Exists(ctx, "host_open:boards-api.greenhouse.io", "host_fail:boards-api.greenhouse.io").Val() != 0 {
		t.Fatal("native HTTP success did not recover its observed API circuit")
	}
	var due time.Time
	var md []byte
	if err := f.pg.QueryRow(ctx, "SELECT next_check_at,metadata FROM job_board WHERE id=$1::uuid", f.board).Scan(&due, &md); err != nil {
		t.Fatal(err)
	}
	score, err := f.r.ZScore(ctx, "monitors_simple:greenhouse", f.board).Result()
	if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("native pipeline changed canonical receipt deadline or lost claim conservation")
	}
	var metadata map[string]any
	if err := json.Unmarshal(md, &metadata); err != nil {
		t.Fatal(err)
	}
	history := metadata["recent_discovered_counts"].([]any)
	if len(history) != 1 || history[0] != float64(1001) {
		t.Fatal("cycle used raw duplicate count as discovered baseline")
	}
}

func TestRealOwnedPipelinePreparationFailureRetainsPrefixAndRejectsAbsence(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	claim, err := f.a.Claim(ctx, queue.Simple)
	if err != nil || claim == nil {
		t.Fatal("owned pipeline claim unavailable")
	}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	preflight, err := f.a.PreflightGreenhouseHost(ctx, claim, circuits)
	if err != nil || preflight.Receipt != nil {
		t.Fatal("native pipeline circuit preflight failed", err)
	}
	cycle, err := f.a.BeginGreenhouseCycle(ctx, claim)
	if err != nil {
		t.Fatal(err)
	}
	inventory := pipelineInventory(1001)
	// Give every fixture its own URL identities; raw generic examples must not
	// collide with another retained company's first-owner rows.
	for i := range inventory.Jobs {
		inventory.Jobs[i].URL = fmt.Sprintf("https://example.com/jobs/%s-%d", f.company, i)
	}
	cause := errors.New("private preparation failure")
	result, err := PersistGreenhouseInventory(ctx, cycle, &pipelinePreparer{failAt: 751, cause: cause}, inventory)
	if result == nil || result.Cycle != nil || result.Batches.Inserted != 500 || !errors.Is(err, cause) {
		t.Fatal("failed preparation returned whole-cycle success")
	}
	var count int
	var active bool
	if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count); err != nil || count != 501 {
		t.Fatal("failed preparation lost first chunk or committed partial second chunk")
	}
	if result, err := cycle.FinishSuccess(ctx, queue.GreenhouseInventorySummary{Discovered: 1001}); result != nil || !errors.Is(err, queue.ErrConfiguration) {
		t.Fatal("preparation failure retained absence finalization authority")
	}
	if err := f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); err != nil || !active {
		t.Fatal("failed inventory removed unseen original")
	}
	terminal, err := cycle.FinishFailureWithHostCircuit(ctx, "native rich preparation failed", preflight.Run, queue.GreenhouseHostObservation{})
	if err != nil {
		t.Fatal(err)
	}
	if err := f.a.Settle(ctx, claim, terminal.Receipt); err != nil {
		t.Fatal(err)
	}
	if f.r.HGet(ctx, "board:"+f.board, "egress_host").Val() != "job-boards.greenhouse.io" || f.r.Get(ctx, "host_fail:job-boards.greenhouse.io").Val() != "1" {
		t.Fatal("preparation failure lost fallback host routing/circuit accounting")
	}
	var strikes int
	var hasHistory bool
	if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,metadata ? 'recent_discovered_counts' FROM job_board WHERE id=$1::uuid", f.board).Scan(&strikes, &hasHistory); err != nil || strikes != 1 || hasHistory {
		t.Fatal("partial failure created a success baseline or lost failure scheduling")
	}
}

func privatePipelineReferenceDSN(t *testing.T, f nativePipelineFixture) string {
	t.Helper()
	ctx := context.Background()
	// Alembic's ordinary fixture has no separately imported production
	// reference snapshot. Reuse native startup's real columns in an owned
	// private schema and load both lookup and SQLite location indexes.
	schema := "pipeline_refs_" + strings.ReplaceAll(fixtureID(t), "-", "")
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err := f.pg.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = f.pg.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	references, err := os.ReadFile("../lightpanda-b0-executor/testdata/startup-fixture.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.pg.Exec(ctx, strings.ReplaceAll(string(references), "public.", quoted+".")); err != nil {
		t.Fatal(err)
	}
	nativeDSN, err := url.Parse(f.dsn)
	if err != nil {
		t.Fatal(err)
	}
	query := nativeDSN.Query()
	query.Set("search_path", schema+",public")
	nativeDSN.RawQuery = query.Encode()
	return nativeDSN.String()
}
