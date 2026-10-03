//go:build integration

package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Only the disposable Actions harness may invoke these installed host commands.
// The preceding containment test owns two sleeping stand-ins, not real workers.
func TestActualInstalledNativeHostQuiescenceJoinsSQLExclusionAndRecoversSIGKILL(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_HOST_QUIESCENCE") != "1" {
		t.Skip("explicit disposable installed SQL quiescence fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" || os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY") == "" {
		t.Fatal("SQL quiescence requires disposable Linux installed image")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 540*time.Second)
	defer cancel()
	const database = "postgresql://crawler:crawler@127.0.0.1:5432/jobseek_ordinary_worker_test?sslmode=disable"
	observer, err := pgxpool.New(ctx, database)
	if err != nil {
		t.Fatal("private migrated observer")
	}
	defer observer.Close()
	source := ordinaryFixtureSourceRevision(t)
	b, err := os.ReadFile(os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_JOIN_PROOF"))
	var proof struct {
		Source       string            `json:"source_revision"`
		ImageID      string            `json:"image_id"`
		Architecture string            `json:"architecture"`
		Binary       string            `json:"binary_sha256"`
		CA           string            `json:"system_ca_sha256"`
		Assets       map[string]string `json:"installed_asset_sha256"`
		Reference    string            `json:"immutable_reference"`
	}
	if err != nil || len(b) > 1<<20 || json.Unmarshal(b, &proof) != nil || proof.Source != source || proof.Architecture != runtime.GOARCH || len(proof.Assets) != 34 {
		t.Fatal("independent SQL fixture installed identity")
	}
	expect := release.InstalledExpectationSpec{Version: "jobseek.crawler-installed-expectation/v1", SourceRevision: source, ImageID: proof.ImageID, Architecture: runtime.GOARCH, Kind: "native-ordinary", BinarySHA256: proof.Binary, CASHA256: proof.CA, Assets: proof.Assets}
	deployment := hostPrivateDirectory(t)
	if os.WriteFile(filepath.Join(deployment, "docker-compose.yml"), []byte("services: {}\n"), 0600) != nil {
		t.Fatal("private spec fixture")
	}
	r := HostPreflightRequest{Version: "jobseek.crawler-host-preflight-request/v1", CoordinatorSource: source, Owner: "colophon-group", Project: "jobseek-native-host-contain", Architecture: runtime.GOARCH, DeploymentDirectory: deployment, Installed: []HostInstalledRequest{}}
	compose := "services:\n  postgres:\n    image: postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193\n    environment:\n      POSTGRES_USER: crawler\n      POSTGRES_PASSWORD: crawler\n      POSTGRES_DB: jobseek_ordinary_worker_test\n      GITHUB_ACTIONS: 'true'\n      CI: 'true'\n"
	for _, service := range []string{"worker", "exporter"} {
		compose += "  " + service + ":\n    image: " + proof.Reference + "\n    user: '10001:10001'\n    entrypoint: ['/bin/sleep']\n    command: ['600']\n"
	}
	for _, role := range []string{"active", "incoming", "rollback"} {
		g, files := releaseExecutableGeneration(t, source)
		if role == "active" {
			g = hostSelectActiveFixture(t, deployment, g)
		}
		newEnv := files["environment.env"] + "LOCAL_DATABASE_URL=" + database + "\n"
		manifest := strings.ReplaceAll(files["release.manifest"], hostDigest([]byte(files["docker-compose.yml"])), hostDigest([]byte(compose)))
		manifest = strings.ReplaceAll(manifest, hostDigest([]byte(files["environment.env"])), hostDigest([]byte(newEnv)))
		for name, body := range map[string]string{"docker-compose.yml": compose, "docker-compose.sha256": hostDigest([]byte(compose)) + "\n", "environment.env": newEnv, "environment.sha256": hostDigest([]byte(newEnv)) + "\n", "release.manifest": manifest} {
			if os.WriteFile(filepath.Join(g, name), []byte(body), 0600) != nil {
				t.Fatal("SQL generation fixture")
			}
		}
		f, err := release.VerifyFiles(ctx, g, r.Owner)
		if err != nil {
			t.Fatal("SQL generation verification", err)
		}
		r.Releases = append(r.Releases, HostReleaseRequest{role, g, f.SHA256()})
	}
	worker, exporter := os.Getenv("JOBSEEK_CRAWLER_RELEASE_CONTAINMENT_WORKER"), os.Getenv("JOBSEEK_CRAWLER_RELEASE_CONTAINMENT_EXPORTER")
	r.Installed = []HostInstalledRequest{{"active", "worker", worker, expect}, {"active", "exporter", exporter, expect}}
	state := hostPrivateDirectory(t)
	body, _ := json.Marshal(r)
	if os.WriteFile(filepath.Join(state, "request.json"), body, 0600) != nil {
		t.Fatal("SQL protected request")
	}
	command := func(operation, intent string) *exec.Cmd {
		cmd := exec.CommandContext(ctx, os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY"), "--"+operation)
		cmd.Env = []string{"ORDINARY_GO_WORKER_MODE=" + operation, "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION=" + source, "ORDINARY_HOST_REQUEST_DIRECTORY=" + state, "ORDINARY_HOST_REQUEST_SHA256=" + hostDigest(body), "ORDINARY_HOST_PREFLIGHT_INTENT_SHA256=" + intent, "LOCAL_DATABASE_URL=invalid", "REDIS_URL=invalid", "DOCKER_HOST=tcp://127.0.0.1:1", "PGSERVICE=caller-sensitive-value", "PGSERVICEFILE=/does-not-exist", "PGOPTIONS=-c search_path=caller-sensitive-value", "PGHOST=invalid"}
		return cmd
	}
	out, err := command("host-preflight", "").CombinedOutput()
	var preflight HostPreflightResult
	if err != nil || json.Unmarshal(out, &preflight) != nil {
		hostContainmentFixtureDiagnostic(t, ctx, r)
		t.Fatal("actual SQL quiescence preflight", err)
	}
	call := func(accept bool) *HostContainmentResult {
		t.Helper()
		out, err := command("host-quiesce", preflight.IntentSHA256).CombinedOutput()
		if !accept {
			if err == nil || strings.TrimSpace(string(out)) != "ordinary host quiescence rejected" || ctx.Err() != nil {
				t.Fatal("live SQL lease admitted", err)
			}
			return nil
		}
		var result HostContainmentResult
		if err != nil || json.Unmarshal(out, &result) != nil || result.SourceRevision != source || result.Operation != "host-quiesce" || result.Phase != "docker_and_sql_quiescence_observed" || !result.SQLBarriersObserved || result.RuntimeAdmission || result.ReceiptSHA256 != hostDigest(result.Receipt) {
			t.Fatal("actual SQL quiescence result", err)
		}
		if bytes.Contains(out, []byte(state)) || bytes.Contains(out, []byte("caller-sensitive-value")) || bytes.Contains(out, []byte(database)) {
			t.Fatal("SQL quiescence disclosed private inputs")
		}
		stored, err := os.ReadFile(filepath.Join(state, "quiescence-"+result.ReceiptSHA256+".json"))
		if err != nil || !bytes.Equal(stored, result.Receipt) {
			t.Fatal("SQL completion before durable receipt")
		}
		return &result
	}
	var company, board string
	if observer.QueryRow(ctx, "SELECT gen_random_uuid()::text,gen_random_uuid()::text").Scan(&company, &board) != nil {
		t.Fatal("private SQL lease identities")
	}
	if _, err := observer.Exec(ctx, "INSERT INTO company(id,slug,name) VALUES($1,$2,'SQL quiescence fixture')", company, "host-sql-"+company); err != nil {
		t.Fatal("private lease company")
	}
	defer func() {
		_, _ = observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1", board)
		_, _ = observer.Exec(context.Background(), "DELETE FROM company WHERE id=$1", company)
	}()
	if _, err := observer.Exec(ctx, "INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,leased_until,lease_owner) VALUES($1,$2,$3,'https://host-sql-fixture.invalid','greenhouse',clock_timestamp()+interval '10 minutes','private-fixture-owner')", board, company, "host-sql-"+board); err != nil {
		t.Fatal("private live lease")
	}
	call(false)
	var owner string
	var live bool
	if observer.QueryRow(ctx, "SELECT lease_owner,leased_until>clock_timestamp() FROM job_board WHERE id=$1", board).Scan(&owner, &live) != nil || owner != "private-fixture-owner" || !live {
		t.Fatal("installed refusal cleared live lease")
	}
	if _, err := observer.Exec(ctx, "UPDATE job_board SET leased_until=clock_timestamp()-interval '1 second' WHERE id=$1", board); err != nil {
		t.Fatal("private expired lease")
	}
	first := call(true)
	var receipt struct {
		SQL struct {
			PID       int32   `json:"backend_pid"`
			Keys      []int64 `json:"exclusive_barriers"`
			Boards    int     `json:"live_board_leases"`
			Postings  int     `json:"live_posting_leases"`
			Admission bool    `json:"runtime_admission"`
		} `json:"sql_exclusion"`
	}
	if json.Unmarshal(first.Receipt, &receipt) != nil || receipt.SQL.PID <= 0 || len(receipt.SQL.Keys) != 3 || receipt.SQL.Boards != 0 || receipt.SQL.Postings != 0 || receipt.SQL.Admission {
		t.Fatal("SQL proof lost scope/held backend")
	}
	assertReleased := func(pid int32) {
		t.Helper()
		deadline := time.Now().Add(5 * time.Second)
		for {
			var n int
			if observer.QueryRow(ctx, "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND pid=$1", pid).Scan(&n) != nil {
				t.Fatal("SQL release observation")
			}
			if n == 0 {
				return
			}
			if time.Now().After(deadline) {
				t.Fatal("dead coordinator retained SQL locks")
			}
			time.Sleep(5 * time.Millisecond)
		}
	}
	assertReleased(receipt.SQL.PID)
	t.Run("in process host callback and failure containment", func(t *testing.T) {
		if ClearHostDatabaseEnvironment() != nil {
			t.Fatal("explicit library coordinator PG environment")
		}
		env := map[string]string{"ORDINARY_GO_WORKER_MODE": "host-quiesce", "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION": source, "ORDINARY_HOST_REQUEST_DIRECTORY": state, "ORDINARY_HOST_REQUEST_SHA256": hostDigest(body), "ORDINARY_HOST_PREFLIGHT_INTENT_SHA256": preflight.IntentSHA256}
		config, err := ReadHostQuiescenceConfig(func(key string) string { return env[key] }, source)
		if err != nil {
			t.Fatal("explicit in-process host request")
		}
		var originalColdContext HostColdPhaseContext
		var escapedPhaseContext context.Context
		var escapedPhasePool *pgxpool.Pool
		for _, reject := range []bool{false, true} {
			var pid int32
			result, err := WithHostQuiescence(ctx, config, func(scoped context.Context, pool *pgxpool.Pool, sql *queue.HostColdSQL) error {
				if queue.CheckHostColdSQLScope(scoped, pool, source) != nil {
					t.Fatal("callback lost bound SQL scope")
				}
				phaseContext, err := InspectHostColdPhaseContext(scoped, pool)
				if err != nil || phaseContext.RuntimeAdmission || phaseContext.Binding.SourceRevision != source || phaseContext.Binding.RequestSHA256 != hostDigest(body) || phaseContext.Binding.ContainmentIntentSHA256 != first.IntentSHA256 || phaseContext.ActiveReleaseSHA256 != r.Releases[0].FileEvidenceSHA256 || phaseContext.TargetReleaseSHA256 != r.Releases[1].FileEvidenceSHA256 || phaseContext.RollbackReleaseSHA256 != r.Releases[2].FileEvidenceSHA256 || !planPattern.MatchString(phaseContext.ColdAttestationSHA256) {
					t.Fatal("opaque phase context lost actual host release/cold binding", err)
				}
				if originalColdContext.Version != "" && originalColdContext != phaseContext {
					t.Fatal("new backend changed exact host cold attestation")
				}
				originalColdContext, escapedPhaseContext, escapedPhasePool = phaseContext, scoped, pool
				// This fixture intentionally has no selected Redis consumer or
				// daemon. A caller URL must never fill that authority gap.
				if WithSelectedHostColdRedis(scoped, pool, func(context.Context) error {
					t.Fatal("caller endpoint filled missing selected Redis execution")
					return nil
				}) == nil || queue.CheckHostColdSQLScope(scoped, pool, source) != nil {
					t.Fatal("missing selected Redis admitted or lost held SQL scope")
				}
				var observed struct {
					PID  int32   `json:"backend_pid"`
					Keys []int64 `json:"exclusive_barriers"`
				}
				if json.Unmarshal([]byte(sql.Body()), &observed) != nil || len(observed.Keys) != 3 {
					t.Fatal("callback SQL observation")
				}
				pid = observed.PID
				for _, key := range observed.Keys {
					var entered bool
					if observer.QueryRow(scoped, "SELECT pg_try_advisory_xact_lock_shared($1)", key).Scan(&entered) != nil || entered {
						t.Fatal("writer entered live host callback")
					}
				}
				lock, err := os.OpenFile(hostMutationLock, os.O_RDWR, 0)
				if err != nil {
					t.Fatal("actual shared host lock observation")
				}
				defer lock.Close()
				if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != syscall.EWOULDBLOCK {
					if err == nil {
						_ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
					}
					t.Fatal("host callback did not hold shared mutation lock")
				}
				if reject {
					return queue.ErrAuthorityLost
				}
				return nil
			})
			if pid <= 0 || reject && (err == nil || result != nil) || !reject && (err != nil || result == nil || !result.SQLBarriersObserved || result.RuntimeAdmission) {
				t.Fatal("in-process callback completion/failure changed contract", err)
			}
			assertReleased(pid)
			if _, err := InspectHostColdPhaseContext(escapedPhaseContext, escapedPhasePool); err == nil {
				t.Fatal("completed callback retained phase authority")
			}
		}
		if retry := call(true); retry.IntentSHA256 != first.IntentSHA256 {
			t.Fatal("failed callback changed retained containment identity")
		}
		t.Log("actual in-process host callback held the shared mutation flock and all three SQL writer barriers; successful and rejected callbacks released their private SQL backend; callback failure retained cold exact-ID writer containment; returned observation grants no runtime admission")
		t.Log("actual host callback issued an opaque cold phase context bound to the selected active/incoming/rollback file evidence and original containment request; cold attestation stable across private SQL backend retries; escaped callback context refused; phase effects and complete installed cold driver remain unproven")
		t.Log("actual host callback refused caller Redis fallback when selected service execution had no Redis consumer/daemon; host/SQL scope remained held; full selected-connection phase handoff unproven")
	})
	// A current shared writer barrier prevents completion until its transaction
	// exits. No runtime fault environment or unsafe direct Docker mutation.
	blocker, err := observer.Acquire(ctx)
	if err != nil {
		t.Fatal("SQL blocker fixture")
	}
	if _, err := blocker.Exec(ctx, "SELECT pg_advisory_lock_shared($1)", receipt.SQL.Keys[0]); err != nil {
		t.Fatal(err)
	}
	child := command("host-quiesce", preflight.IntentSHA256)
	var output bytes.Buffer
	child.Stdout, child.Stderr = &output, &output
	if child.Start() != nil {
		t.Fatal("blocked actual SQL child")
	}
	done := make(chan error, 1)
	go func() { done <- child.Wait() }()
	defer func() {
		_ = child.Process.Kill()
		_, _ = blocker.Exec(context.Background(), "SELECT pg_advisory_unlock_shared($1)", receipt.SQL.Keys[0])
		blocker.Release()
	}()
	deadline := time.Now().Add(90 * time.Second)
	for {
		var attempted bool
		if observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='jobseek:host:cold-coordinator' AND query LIKE '%pg_try_advisory_lock%')").Scan(&attempted) != nil {
			t.Fatal("actual SQL wait observation")
		}
		select {
		case err := <-done:
			t.Fatal("shared SQL barrier did not prevent child completion", err)
		default:
		}
		if attempted {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("shared writer wait seam timeout")
		}
		time.Sleep(5 * time.Millisecond)
	}
	if _, err := blocker.Exec(ctx, "SELECT pg_advisory_unlock_shared($1)", receipt.SQL.Keys[0]); err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatal("actual SQL child did not resume", err)
	}
	var resumed HostContainmentResult
	if json.Unmarshal(output.Bytes(), &resumed) != nil || !resumed.SQLBarriersObserved || resumed.RuntimeAdmission || resumed.IntentSHA256 != first.IntentSHA256 {
		t.Fatal("shared writer wait resumed without exact SQL proof")
	}
	t.Run("SQL session SIGKILL", func(t *testing.T) {
		child := command("host-quiesce", preflight.IntentSHA256)
		var output bytes.Buffer
		child.Stdout, child.Stderr = &output, &output
		if child.Start() != nil {
			t.Fatal("SQL crash child")
		}
		defer func() { _ = child.Process.Kill() }()
		done := make(chan error, 1)
		go func() { done <- child.Wait() }()
		deadline := time.Now().Add(90 * time.Second)
		for {
			var pid *int32
			err := observer.QueryRow(ctx, `SELECT min(pid) FROM (SELECT l.pid FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE l.locktype='advisory' AND l.mode='ExclusiveLock' AND l.granted AND a.application_name='jobseek:host:cold-coordinator' GROUP BY l.pid HAVING count(*)=3) held`).Scan(&pid)
			if err != nil {
				t.Fatal("actual held SQL backend observation")
			}
			if pid != nil {
				if child.Process.Kill() != nil {
					t.Fatal("kernel SQL child crash")
				}
				if err := <-done; err == nil {
					t.Fatal("SQL SIGKILL succeeded")
				}
				assertReleased(*pid)
				return
			}
			select {
			case err := <-done:
				t.Fatal("SQL child exited before held-session seam", err)
			default:
			}
			if time.Now().After(deadline) {
				t.Fatal("SQL SIGKILL seam timeout")
			}
			time.Sleep(5 * time.Millisecond)
		}
	})
	retry := call(true)
	if retry.IntentSHA256 != first.IntentSHA256 {
		t.Fatal("SQL crash retry adopted different container intent")
	}
	t.Log("actual installed native host quiescence joined selected protected database config to observed cold Docker state under the shared host lock; held ordinary/routing/CDC SQL session barriers and zero live SQL leases through durable receipt; live lease refused without clearing; caller PG settings cleared; observed shared writer wait and kernel SQL-session SIGKILL release/retry verified; sleeping fixture writers only; runtime admission false and returned receipt is past observation")
}
