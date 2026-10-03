package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"path/filepath"
	"runtime"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
	"github.com/jackc/pgx/v5/pgxpool"
)

type HostContainmentConfig struct {
	preflight HostPreflightConfig
	intentSHA string
}

func ReadHostContainmentConfig(getenv func(string) string, source string) (HostContainmentConfig, error) {
	if getenv == nil || getenv("ORDINARY_GO_WORKER_MODE") != "host-contain" || !planPattern.MatchString(getenv("ORDINARY_HOST_PREFLIGHT_INTENT_SHA256")) {
		return HostContainmentConfig{}, errHostPreflight
	}
	c, err := ReadHostPreflightConfig(func(key string) string {
		if key == "ORDINARY_GO_WORKER_MODE" {
			return "host-preflight"
		}
		return getenv(key)
	}, source)
	if err != nil {
		return HostContainmentConfig{}, errHostPreflight
	}
	return HostContainmentConfig{c, getenv("ORDINARY_HOST_PREFLIGHT_INTENT_SHA256")}, nil
}

type hostContainmentIntent struct {
	Version               string          `json:"version"`
	SourceRevision        string          `json:"source_revision"`
	RequestSHA256         string          `json:"request_sha256"`
	PreflightIntentSHA256 string          `json:"preflight_intent_sha256"`
	SelectionSHA256       string          `json:"selected_active_sha256"`
	ArchiveSHA256         string          `json:"archive_sha256"`
	CaptureSHA256         string          `json:"capture_sha256"`
	PlanSHA256            string          `json:"plan_sha256"`
	Plan                  json.RawMessage `json:"plan"`
}

type HostContainmentResult struct {
	Version             string          `json:"version"`
	Operation           string          `json:"operation"`
	SourceRevision      string          `json:"source_revision"`
	Phase               string          `json:"phase"`
	RuntimeAdmission    bool            `json:"runtime_admission"`
	SQLBarriersObserved bool            `json:"sql_barriers_observed"`
	RequestSHA256       string          `json:"request_sha256"`
	IntentSHA256        string          `json:"intent_sha256"`
	ReceiptSHA256       string          `json:"receipt_sha256"`
	Receipt             json.RawMessage `json:"receipt"`
}

func canonicalHostDecode(body []byte, target any) error {
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(target) != nil || d.Decode(new(any)) != io.EOF {
		return errHostPreflight
	}
	b, err := json.Marshal(target)
	if err != nil || !bytes.Equal(b, body) {
		return errHostPreflight
	}
	return nil
}

func RunHostContainment(ctx context.Context, c HostContainmentConfig) (*HostContainmentResult, error) {
	if runtime.GOOS != "linux" {
		return nil, errHostPreflight
	}
	return runHostContainment(ctx, c, hostMutationLock, observeHostPreflight, release.ContainWriters, nil)
}

type hostContainEffect func(context.Context, *release.WriterContainmentPlan, []*release.Images, bool, func() error, func() error) (*release.ColdContainers, error)

func runHostContainment(ctx context.Context, c HostContainmentConfig, lockPath string, observe func(context.Context, HostPreflightRequest) (*hostObservations, error), contain hostContainEffect, hook func(string) error) (*HostContainmentResult, error) {
	return runHostContainmentPhase(ctx, c, lockPath, observe, contain, hook, false, nil)
}

func runHostContainmentPhase(ctx context.Context, c HostContainmentConfig, lockPath string, observe func(context.Context, HostPreflightRequest) (*hostObservations, error), contain hostContainEffect, hook func(string) error, withSQL bool, driveCold func(context.Context, *pgxpool.Pool, *queue.HostColdSQL) error) (*HostContainmentResult, error) {
	p := c.preflight
	if ctx == nil || ctx.Err() != nil || !planPattern.MatchString(c.intentSHA) || !cleanHostPath(p.directory) || !planPattern.MatchString(p.expected) || !sourcePattern.MatchString(p.source) || observe == nil || contain == nil || driveCold != nil && !withSQL {
		return nil, errHostPreflight
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	lock, err := acquireHostLock(ctx, lockPath)
	if err != nil {
		return nil, errHostPreflight
	}
	defer lock.Close()
	store, err := openHostStore(p.directory)
	if err != nil {
		return nil, errHostPreflight
	}
	defer store.Close()
	body, err := store.read("request.json", false)
	if err != nil {
		return nil, errHostPreflight
	}
	r, err := decodeHostRequest(body, p.expected, p.source)
	if err != nil || r.Architecture != runtime.GOARCH {
		return nil, errHostPreflight
	}
	for _, g := range r.Releases {
		resolved, err := filepath.EvalSymlinks(g.Directory)
		if err != nil || resolved != g.Directory || pathInside(g.Directory, p.directory) {
			return nil, errHostPreflight
		}
	}
	preflight, err := store.read("intent.json", false)
	var original hostPreflightIntent
	if err != nil || hostDigest(preflight) != c.intentSHA || canonicalHostDecode(preflight, &original) != nil || original.Version != "jobseek.crawler-host-preflight-intent/v1" || original.SourceRevision != p.source || original.RequestSHA256 != p.expected {
		return nil, errHostPreflight
	}
	archive, err := store.readLimit("deploy-specs.tar", false, 48<<20)
	if err != nil || hostDigest(archive) != original.ArchiveSHA256 {
		return nil, errHostPreflight
	}
	if _, err := release.DecodeSpecArchive(ctx, archive, original.ArchiveSHA256); err != nil {
		return nil, errHostPreflight
	}
	bound := func(o *hostObservations) bool {
		return o != nil && o.Selection != nil && o.Specs != nil && o.Inventory != nil && o.Execution != nil && len(o.Images) > 0 && len(o.Releases) == 3 && len(o.Installed) == len(r.Installed) && o.Selection.SHA256() == original.SelectionSHA256 && o.Specs.SHA256() == original.CaptureSHA256 && o.Specs.ArchiveSHA256() == original.ArchiveSHA256 && ctx.Err() == nil
	}
	o, err := observe(ctx, r)
	if err != nil || !bound(o) {
		return nil, errHostPreflight
	}
	var pool *pgxpool.Pool
	if withSQL {
		active := r.Releases[0]
		config, err := release.VerifiedDatabaseConfig(ctx, active.Directory, r.Owner, active.FileEvidenceSHA256)
		if err != nil {
			return nil, errHostPreflight
		}
		pool, err = pgxpool.NewWithConfig(ctx, config)
		if err != nil {
			return nil, errHostPreflight
		}
		defer pool.Close()
		if pool.Ping(ctx) != nil {
			return nil, errHostPreflight
		}
	}
	intentBytes, err := store.read("containment-intent.json", true)
	var intent hostContainmentIntent
	var plan *release.WriterContainmentPlan
	if errors.Is(err, fs.ErrNotExist) {
		plan, err = release.PlanWriterContainment(ctx, o.Inventory, o.Images, r.Project)
		if err != nil {
			return nil, errHostPreflight
		}
		// Every regular target has installed-file evidence joined to the exact
		// current service/image/source. An empty preflight set cannot authorize
		// stopping writers. One-offs cannot use this request shape and refuse.
		covered := map[string]bool{}
		for _, item := range r.Installed {
			covered[item.ContainerID] = true
		}
		for _, id := range plan.TargetIDs() {
			if !covered[id] {
				return nil, errHostPreflight
			}
		}
		intent = hostContainmentIntent{"jobseek.crawler-host-containment-intent/v1", p.source, p.expected, c.intentSHA, original.SelectionSHA256, original.ArchiveSHA256, original.CaptureSHA256, plan.SHA256(), json.RawMessage(plan.Body())}
		intentBytes, err = json.Marshal(intent)
		if err != nil || lock.verify() != nil || store.verify() != nil || store.retain("containment-intent.json", intentBytes, hook) != nil {
			return nil, errHostPreflight
		}
	} else {
		if err != nil || canonicalHostDecode(intentBytes, &intent) != nil || intent.Version != "jobseek.crawler-host-containment-intent/v1" || intent.SourceRevision != p.source || intent.RequestSHA256 != p.expected || intent.PreflightIntentSHA256 != c.intentSHA || intent.SelectionSHA256 != original.SelectionSHA256 || intent.ArchiveSHA256 != original.ArchiveSHA256 || intent.CaptureSHA256 != original.CaptureSHA256 {
			return nil, errHostPreflight
		}
		plan, err = release.DecodeWriterContainmentPlan(intent.Plan, intent.PlanSHA256)
		if err != nil || store.retain("containment-intent.json", intentBytes, hook) != nil {
			return nil, errHostPreflight
		}
		covered := map[string]bool{}
		for _, item := range r.Installed {
			covered[item.ContainerID] = true
		}
		for _, id := range plan.TargetIDs() {
			if !covered[id] {
				return nil, errHostPreflight
			}
		}
	}
	if hook != nil && hook("containment_intent_retained") != nil {
		return nil, errHostPreflight
	}
	// Fresh selected files/specs/images/installed bytes are reobserved before
	// every effect. Repeated whole observation never executes SQL or Redis.
	var guarded *hostObservations
	guard := func() error {
		if ctx.Err() != nil || lock.verify() != nil || store.verify() != nil {
			return errHostPreflight
		}
		for name, expected := range map[string][]byte{"request.json": body, "intent.json": preflight, "containment-intent.json": intentBytes} {
			b, err := store.read(name, false)
			if err != nil || !bytes.Equal(b, expected) {
				return errHostPreflight
			}
		}
		b, err := store.readLimit("deploy-specs.tar", false, 48<<20)
		if err != nil || !bytes.Equal(b, archive) {
			return errHostPreflight
		}
		fresh, err := observe(ctx, r)
		if err != nil || !bound(fresh) {
			return errHostPreflight
		}
		for n := range o.Releases {
			if o.Releases[n].ImagesSHA != fresh.Releases[n].ImagesSHA {
				return errHostPreflight
			}
		}
		guarded = fresh
		return lock.verify()
	}
	barrier, _ := json.Marshal(struct {
		Version      string `json:"version"`
		IntentSHA256 string `json:"intent_sha256"`
	}{"jobseek.crawler-host-restarts-disabled/v1", hostDigest(intentBytes)})
	disabled := false
	if b, err := store.read("restarts-disabled.json", true); err == nil {
		if !bytes.Equal(b, barrier) || store.retain("restarts-disabled.json", barrier, hook) != nil {
			return nil, errHostPreflight
		}
		disabled = true
	} else if !errors.Is(err, fs.ErrNotExist) {
		return nil, errHostPreflight
	}
	checkpoint := func() error {
		if guard() != nil || store.retain("restarts-disabled.json", barrier, hook) != nil {
			return errHostPreflight
		}
		if hook != nil {
			return hook("restarts_disabled_retained")
		}
		return nil
	}
	cold, err := contain(ctx, plan, o.Images, disabled, guard, checkpoint)
	if err != nil || cold == nil || guard() != nil {
		return nil, errHostPreflight
	}
	if hook != nil && hook("docker_writers_contained") != nil {
		return nil, errHostPreflight
	}
	fresh, err := observe(ctx, r)
	if err != nil || !bound(fresh) {
		return nil, errHostPreflight
	}
	actual, err := release.RequireColdContainers(ctx, fresh.Inventory, fresh.Images)
	if err != nil || actual.SHA256() != cold.SHA256() {
		return nil, errHostPreflight
	}
	// Acquiring SQL can wait for an existing writer. Recheck actual cold daemon
	// state inside that live session and on both sides of receipt publication;
	// a previously observed cold inventory cannot authorize the later overlap.
	coldGuard := func() error {
		if guard() != nil {
			return errHostPreflight
		}
		current, err := release.RequireColdContainers(ctx, guarded.Inventory, guarded.Images)
		if err != nil || current.SHA256() != actual.SHA256() {
			return errHostPreflight
		}
		return nil
	}
	finish := func(sqlCtx context.Context, sql *queue.HostColdSQL) (*HostContainmentResult, error) {
		operation, phase, prefix := "host-contain", "docker_writers_contained", "containment-"
		var sqlBody json.RawMessage
		if sql != nil {
			if sql.Check(sqlCtx) != nil {
				return nil, errHostPreflight
			}
			operation, phase, prefix = "host-quiesce", "docker_and_sql_quiescence_observed", "quiescence-"
			sqlBody = json.RawMessage(sql.Body())
		}
		receipt, err := json.Marshal(struct {
			Version              string          `json:"version"`
			RuntimeAdmission     bool            `json:"runtime_admission"`
			SQLBarriersObserved  bool            `json:"sql_barriers_observed"`
			RequestSHA256        string          `json:"request_sha256"`
			IntentSHA256         string          `json:"intent_sha256"`
			RestartBarrierSHA256 string          `json:"restart_barrier_sha256"`
			ColdDocker           json.RawMessage `json:"cold_docker"`
			Inventory            json.RawMessage `json:"inventory"`
			SQL                  json.RawMessage `json:"sql_exclusion,omitempty"`
		}{"jobseek.crawler-host-containment-observation/v1", false, sql != nil, p.expected, hostDigest(intentBytes), hostDigest(barrier), json.RawMessage(actual.Body()), json.RawMessage(fresh.Inventory.Body()), sqlBody})
		if err != nil || coldGuard() != nil || store.retain(prefix+hostDigest(receipt)+".json", receipt, hook) != nil || coldGuard() != nil {
			return nil, errHostPreflight
		}
		if sql != nil && sql.Check(sqlCtx) != nil {
			return nil, errHostPreflight
		}
		return &HostContainmentResult{"jobseek.crawler-host-containment-result/v1", operation, p.source, phase, false, sql != nil, p.expected, hostDigest(intentBytes), hostDigest(receipt), receipt}, nil
	}
	if !withSQL {
		return finish(ctx, nil)
	}
	var result *HostContainmentResult
	binding := queue.HostColdSQLBinding{SourceRevision: p.source, RequestSHA256: p.expected, ContainmentIntentSHA256: hostDigest(intentBytes)}
	err = queue.WithHostColdSQL(ctx, pool, binding, func(sqlCtx context.Context, sql *queue.HostColdSQL) error {
		if guard() != nil {
			return errHostPreflight
		}
		if hook != nil && hook("sql_barriers_held") != nil {
			return errHostPreflight
		}
		if driveCold != nil {
			if coldGuard() != nil || sql.Check(sqlCtx) != nil {
				return errHostPreflight
			}
			if withHostColdPhaseScope(sqlCtx, store, pool, sql, binding, r.Releases, actual.SHA256(), coldGuard, func(phaseCtx context.Context) error {
				scope := phaseCtx.Value(hostColdPhaseKey{}).(*hostColdPhaseScope)
				scope.redis = func() (*release.RedisEndpoint, error) {
					if coldGuard() != nil {
						return nil, errHostPreflight
					}
					return release.RequireSelectedRedisEndpoint(phaseCtx, r.Releases[0].FileEvidenceSHA256, guarded.Inventory, guarded.Images, guarded.Execution)
				}
				return driveCold(phaseCtx, pool, sql)
			}) != nil || coldGuard() != nil || sql.Check(sqlCtx) != nil {
				return errHostPreflight
			}
		}
		var err error
		result, err = finish(sqlCtx, sql)
		return err
	})
	if err != nil {
		return nil, errHostPreflight
	}
	return result, nil
}
