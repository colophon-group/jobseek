//go:build densitybench

package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"runtime"
	"sort"
	"strings"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

const (
	densitySchemaVersion  = 1
	densityOriginCount    = 8
	densityWaveCount      = 2
	densityTasksPerWave   = 8
	densityTaskCount      = 16
	densityNavigationTime = 20 * time.Second
	densityJobTime        = 25 * time.Second
	densityShutdownTime   = 45 * time.Second
	densityFileLimit      = 128 << 10
)

var errDensityConfig = errors.New("invalid density benchmark configuration")

//go:embed density/workload.v1.json
var embeddedDensityWorkload []byte

type densityWorkload struct {
	SchemaVersion int           `json:"schema_version"`
	OriginCount   int           `json:"origin_count"`
	Waves         []densityWave `json:"waves"`
}

type densityWave struct {
	ID    string        `json:"id"`
	Tasks []densityTask `json:"tasks"`
}

type densityTask struct {
	ID                       string  `json:"id"`
	OriginID                 string  `json:"origin_id"`
	Mode                     string  `json:"mode"`
	RelativePath             string  `json:"relative_path"`
	Status                   int     `json:"status"`
	ResponseBody             string  `json:"response_body"`
	ExpectedOuterHTMLSize    int     `json:"expected_outer_html_size"`
	ExpectedOuterHTMLSHA256  string  `json:"expected_outer_html_sha256"`
	ExpectedEvaluationJSON   *string `json:"expected_evaluation_json"`
	ExpectedEvaluationSHA256 *string `json:"expected_evaluation_sha256"`
}

type densityReport struct {
	SchemaVersion    int                 `json:"schema_version"`
	ImplementationID string              `json:"implementation_id"`
	RuntimeID        string              `json:"runtime_id"`
	SourceCommit     string              `json:"source_commit"`
	ImageIdentity    string              `json:"image_identity"`
	WorkloadSHA256   string              `json:"workload_sha256"`
	Concurrency      int                 `json:"concurrency"`
	Succeeded        bool                `json:"succeeded"`
	FailureID        string              `json:"failure_id,omitempty"`
	ElapsedNS        int64               `json:"elapsed_ns"`
	Submitted        int                 `json:"submitted"`
	Accepted         uint64              `json:"accepted"`
	Terminal         int                 `json:"terminal"`
	SucceededJobs    int                 `json:"succeeded_jobs"`
	FailedJobs       int                 `json:"failed_jobs"`
	OracleMatches    int                 `json:"oracle_matches"`
	ConservationOK   bool                `json:"conservation_ok"`
	OracleOK         bool                `json:"oracle_ok"`
	MaxInFlightOK    bool                `json:"max_in_flight_ok"`
	ZeroPanicResidue bool                `json:"zero_panic_residue"`
	Waves            []densityWaveReport `json:"waves"`
	Pool             densityPoolReport   `json:"pool"`
}

type densityWaveReport struct {
	ID            string             `json:"id"`
	Succeeded     bool               `json:"succeeded"`
	Submitted     int                `json:"submitted"`
	Terminal      int                `json:"terminal"`
	SucceededJobs int                `json:"succeeded_jobs"`
	FailedJobs    int                `json:"failed_jobs"`
	OracleMatches int                `json:"oracle_matches"`
	MaxInFlight   int                `json:"max_in_flight"`
	Jobs          []densityJobReport `json:"jobs"`
}

type densityJobReport struct {
	ID                string `json:"id"`
	ModeID            string `json:"mode_id"`
	Terminal          bool   `json:"terminal"`
	Succeeded         bool   `json:"succeeded"`
	OracleMatch       bool   `json:"oracle_match"`
	FailureID         string `json:"failure_id,omitempty"`
	FinalURLMatch     bool   `json:"final_url_match"`
	Status            uint32 `json:"status"`
	StatusMatch       bool   `json:"status_match"`
	HTMLBytes         int    `json:"html_bytes"`
	HTMLSHA256        string `json:"html_sha256,omitempty"`
	HTMLMatch         bool   `json:"html_match"`
	EvaluationPresent bool   `json:"evaluation_present"`
	EvaluationBytes   int    `json:"evaluation_bytes"`
	EvaluationSHA256  string `json:"evaluation_sha256,omitempty"`
	EvaluationMatch   bool   `json:"evaluation_match"`
}

type densityPoolReport struct {
	Accepted          uint64 `json:"accepted"`
	Completed         uint64 `json:"completed"`
	Panics            uint64 `json:"panics"`
	Queued            int64  `json:"queued"`
	InFlight          int64  `json:"in_flight"`
	MaxQueued         int64  `json:"max_queued"`
	MaxInFlight       int64  `json:"max_in_flight"`
	ReservedPortCount int    `json:"reserved_port_count"`
}

type densityRawPrivacy struct{}

func (densityRawPrivacy) SealEvaluation(ctx context.Context, _ string, raw []byte, limit uint64) (*runtimev1.ExtensionEnvelope, error) {
	if err := ctx.Err(); err != nil || uint64(len(raw)) > limit || !json.Valid(raw) {
		return nil, errDensityConfig
	}
	digest := sha256.Sum256(raw)
	return &runtimev1.ExtensionEnvelope{
		SchemaId:      "jobseek.browser.evaluation-json",
		SchemaVersion: 1,
		Encoding:      runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON,
		Payload:       append([]byte(nil), raw...),
		PayloadSha256: hex.EncodeToString(digest[:]),
	}, nil
}

func loadDensityWorkload(workloadPath string) (densityWorkload, string, error) {
	workloadBytes, workloadSHA, err := loadExactDensityFile(workloadPath, embeddedDensityWorkload)
	if err != nil {
		return densityWorkload{}, "", err
	}
	var workload densityWorkload
	if decodeDensityJSON(workloadBytes, &workload) != nil || validateDensityWorkload(workload) != nil {
		return densityWorkload{}, "", errDensityConfig
	}
	return workload, workloadSHA, nil
}

func loadExactDensityFile(path string, expected []byte) ([]byte, string, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, "", errDensityConfig
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > densityFileLimit {
		return nil, "", errDensityConfig
	}
	value, err := io.ReadAll(io.LimitReader(file, densityFileLimit+1))
	if err != nil || !bytes.Equal(value, expected) {
		return nil, "", errDensityConfig
	}
	digest := sha256.Sum256(value)
	return value, hex.EncodeToString(digest[:]), nil
}

func decodeDensityJSON(value []byte, output any) error {
	decoder := json.NewDecoder(bytes.NewReader(value))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(output); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return errDensityConfig
	}
	return nil
}

func validateDensityWorkload(workload densityWorkload) error {
	if workload.SchemaVersion != 1 || workload.OriginCount != densityOriginCount || len(workload.Waves) != densityWaveCount {
		return errDensityConfig
	}
	seenTasks := map[string]bool{}
	modes := map[string]string{}
	for waveIndex, wave := range workload.Waves {
		waveID := fmt.Sprintf("w%d", waveIndex)
		if wave.ID != waveID || len(wave.Tasks) != densityTasksPerWave {
			return errDensityConfig
		}
		seenOrigins := map[string]bool{}
		b0 := 0
		for originIndex, task := range wave.Tasks {
			originID := fmt.Sprintf("origin-%d", originIndex)
			expectedOuter := fmt.Sprintf("<html><head><title>%s</title></head><body><main id=\"fixture\">%s</main></body></html>", task.ID, task.ID)
			if task.OriginID != originID || seenOrigins[originID] || seenTasks[task.ID] || task.Status != 201 ||
				(task.Mode != "b0" && task.Mode != "b1") || task.ID != waveID+"-"+originID+"-"+task.Mode ||
				task.RelativePath != "/density/"+waveID+"/"+originID+"/"+task.Mode || !densityASCII(task.ResponseBody) ||
				task.ResponseBody != "<!doctype html>"+expectedOuter {
				return errDensityConfig
			}
			outer := strings.TrimPrefix(task.ResponseBody, "<!doctype html>")
			digest := sha256.Sum256([]byte(outer))
			if outer == task.ResponseBody || len(outer) != task.ExpectedOuterHTMLSize || hex.EncodeToString(digest[:]) != task.ExpectedOuterHTMLSHA256 {
				return errDensityConfig
			}
			if task.Mode == "b0" {
				b0++
				if task.ExpectedEvaluationJSON != nil || task.ExpectedEvaluationSHA256 != nil {
					return errDensityConfig
				}
			} else {
				if task.ExpectedEvaluationJSON == nil || task.ExpectedEvaluationSHA256 == nil || !densityASCII(*task.ExpectedEvaluationJSON) {
					return errDensityConfig
				}
				var title string
				if json.Unmarshal([]byte(*task.ExpectedEvaluationJSON), &title) != nil || title != task.ID {
					return errDensityConfig
				}
				compact, err := json.Marshal(title)
				if err != nil || string(compact) != *task.ExpectedEvaluationJSON || densitySHA(compact) != *task.ExpectedEvaluationSHA256 {
					return errDensityConfig
				}
			}
			seenOrigins[originID], seenTasks[task.ID] = true, true
			if prior, ok := modes[originID]; ok && prior == task.Mode {
				return errDensityConfig
			}
			modes[originID] = task.Mode
		}
		if b0 != 4 {
			return errDensityConfig
		}
	}
	if len(seenTasks) != densityTaskCount {
		return errDensityConfig
	}
	return nil
}

func runDensityBenchmark(ctx context.Context, workload densityWorkload, workloadSHA string, concurrency int, sourceCommit, imageIdentity string, adapter *lightpandaadapter.Adapter) densityReport {
	safeSourceCommit, safeImageIdentity := sourceCommit, imageIdentity
	if !densityLowerHex(safeSourceCommit, 40) {
		safeSourceCommit = ""
	}
	if !densityImageIdentity(safeImageIdentity) {
		safeImageIdentity = ""
	}
	report := densityReport{SchemaVersion: 1, ImplementationID: "go-lightpanda", RuntimeID: runtime.Version(), SourceCommit: safeSourceCommit, ImageIdentity: safeImageIdentity, WorkloadSHA256: workloadSHA, Concurrency: concurrency, Waves: []densityWaveReport{}}
	if ctx == nil || validateDensityWorkload(workload) != nil || !densityAllowedConcurrency(concurrency) ||
		!densityLowerHex(sourceCommit, 40) || !densityImageIdentity(imageIdentity) || adapter == nil {
		report.FailureID = "configuration_invalid"
		return report
	}
	pool, err := NewLightpandaPool(adapter, PoolConfig{Workers: concurrency, Capacity: densityTaskCount, ResultCapacity: densityTaskCount, JobTimeout: densityJobTime, ShutdownGrace: densityShutdownTime})
	if err != nil {
		report.FailureID = "pool_initialization"
		return report
	}
	drained := make(chan PoolResult, densityTaskCount)
	go func() {
		defer close(drained)
		for result := range pool.Results() {
			drained <- result
		}
	}()
	measuredStarted := time.Now()
	measuredFinished := measuredStarted

	for _, wave := range workload.Waves {
		waveReport := densityWaveReport{ID: wave.ID, Jobs: make([]densityJobReport, len(wave.Tasks))}
		tasks := make(map[string]densityTask, len(wave.Tasks))
		for index, task := range wave.Tasks {
			tasks[task.ID] = task
			waveReport.Jobs[index] = densityJobReport{ID: task.ID, ModeID: task.Mode, FailureID: "not_submitted"}
			if pool.Submit(ctx, PoolJob{ID: task.ID, Context: ctx, Timeout: densityJobTime, Input: densityInput(task)}) == nil {
				waveReport.Submitted++
				report.Submitted++
			}
		}
		results := make(map[string]PoolResult, waveReport.Submitted)
		for range waveReport.Submitted {
			result := <-drained
			waveReport.Terminal++
			report.Terminal++
			if _, ok := tasks[result.JobID]; ok {
				if _, duplicate := results[result.JobID]; !duplicate {
					results[result.JobID] = result
				}
			}
		}
		if wave.ID == workload.Waves[len(workload.Waves)-1].ID {
			measuredFinished = time.Now()
		}
		intervals := make([]PoolResult, 0, len(results))
		for index, task := range wave.Tasks {
			if result, ok := results[task.ID]; ok {
				waveReport.Jobs[index] = densityJobResult(task, result)
				intervals = append(intervals, result)
			}
			job := waveReport.Jobs[index]
			if job.Succeeded {
				waveReport.SucceededJobs++
			}
			if job.OracleMatch {
				waveReport.OracleMatches++
			}
		}
		waveReport.FailedJobs = len(wave.Tasks) - waveReport.SucceededJobs
		waveReport.MaxInFlight = densityMaximumOverlap(intervals)
		waveReport.Succeeded = waveReport.Submitted == densityTasksPerWave && waveReport.Terminal == densityTasksPerWave && waveReport.OracleMatches == densityTasksPerWave
		report.SucceededJobs += waveReport.SucceededJobs
		report.OracleMatches += waveReport.OracleMatches
		report.Waves = append(report.Waves, waveReport)
	}
	report.ElapsedNS = measuredFinished.Sub(measuredStarted).Nanoseconds()
	pool.Close()
	for range drained {
		report.Terminal++
	}
	stats := pool.Stats()
	reserved := densityReservedPorts()
	report.Accepted = stats.Accepted
	report.FailedJobs = densityTaskCount - report.SucceededJobs
	report.Pool = densityPoolReport{Accepted: stats.Accepted, Completed: stats.Completed, Panics: stats.Panics, Queued: stats.Queued, InFlight: stats.InFlight, MaxQueued: stats.MaxQueued, MaxInFlight: stats.MaxInFlight, ReservedPortCount: reserved}
	report.ConservationOK = report.Submitted == densityTaskCount && report.Terminal == densityTaskCount && stats.Accepted == densityTaskCount && stats.Completed == densityTaskCount
	report.OracleOK = report.OracleMatches == densityTaskCount
	report.MaxInFlightOK = stats.MaxInFlight == int64(concurrency)
	report.ZeroPanicResidue = stats.Panics == 0 && stats.Queued == 0 && stats.InFlight == 0 && reserved == 0
	report.Succeeded = report.ConservationOK && report.OracleOK && report.MaxInFlightOK && report.ZeroPanicResidue && report.FailedJobs == 0
	if !report.Succeeded {
		report.FailureID = "invariant_failure"
	}
	return report
}

func densityInput(task densityTask) *runtimev1.BrowserExecutionInput {
	target := "http://" + task.OriginID + ".bench.test:8080" + task.RelativePath
	originRequestID := task.ID + "-navigation"
	capabilities := []runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER}
	var evaluations []*runtimev1.EvaluationPlan
	if task.Mode == "b1" {
		capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE)
		evaluations = []*runtimev1.EvaluationPlan{{EvaluationId: task.ID + "-title", Expression: "document.title", MaxResultBytes: 1024, NetworkEffect: runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_NONE}}
	}
	return &runtimev1.BrowserExecutionInput{Assignment: &runtimev1.BrowserAssignment{Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA, CapabilityClass: runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION, ServiceLane: runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_LIGHTPANDA, RoutingRevision: "densitybench-v1"}, Plan: &runtimev1.BrowserPlan{ContractVersion: "crawler.runtime/v1", TargetUrl: target, RequiredCapabilities: capabilities, Navigation: &runtimev1.NavigationPlan{WaitUntil: runtimev1.WaitCondition_WAIT_CONDITION_LOAD, TimeoutMs: uint64(densityNavigationTime.Milliseconds()), OriginRequestId: originRequestID}, Evaluations: evaluations, OriginOperations: []*runtimev1.OriginOperationRef{{OriginRequestId: originRequestID, OperationSequence: 1, Role: "navigation", RequestFingerprint: densitySHA([]byte(target))}}}}
}

func densityJobResult(task densityTask, result PoolResult) densityJobReport {
	report := densityJobReport{ID: task.ID, ModeID: task.Mode, Terminal: true}
	if result.Err != nil || result.Runtime == nil {
		report.FailureID = "pool_error"
		return report
	}
	success := result.Runtime.GetSuccess()
	if success == nil {
		if result.Runtime.GetUnsupported() != nil {
			report.FailureID = "unsupported"
		} else {
			report.FailureID = "runtime_error"
		}
		return report
	}
	report.Succeeded = true
	report.FinalURLMatch = success.FinalUrl == densityInput(task).Plan.TargetUrl
	report.Status = success.GetStatus()
	report.StatusMatch = report.Status == uint32(task.Status)
	body, ok := densityManifestBody(success.Html)
	report.HTMLBytes = len(body)
	if ok {
		report.HTMLSHA256 = densitySHA(body)
	}
	report.HTMLMatch = ok && report.HTMLBytes == task.ExpectedOuterHTMLSize && report.HTMLSHA256 == task.ExpectedOuterHTMLSHA256
	if len(success.Evaluations) == 1 && success.Evaluations[0] != nil && success.Evaluations[0].Value != nil {
		report.EvaluationPresent = true
		payload := success.Evaluations[0].Value.Payload
		report.EvaluationBytes = len(payload)
		report.EvaluationSHA256 = densitySHA(payload)
	}
	if task.Mode == "b0" {
		report.EvaluationMatch = len(success.Evaluations) == 0
	} else {
		report.EvaluationMatch = report.EvaluationPresent && len(success.Evaluations) == 1 && success.Evaluations[0].EvaluationId == task.ID+"-title" && task.ExpectedEvaluationJSON != nil && task.ExpectedEvaluationSHA256 != nil && string(success.Evaluations[0].Value.Payload) == *task.ExpectedEvaluationJSON && report.EvaluationSHA256 == *task.ExpectedEvaluationSHA256
	}
	report.OracleMatch = report.FinalURLMatch && report.StatusMatch && report.HTMLMatch && report.EvaluationMatch
	if !report.OracleMatch {
		report.FailureID = "oracle_mismatch"
	}
	return report
}

func densityManifestBody(manifest *runtimev1.ChunkManifest) ([]byte, bool) {
	if manifest == nil || !manifest.Complete || manifest.TotalSizeBytes > lightpandaadapter.HTMLPayloadLimit {
		return nil, false
	}
	body := make([]byte, 0, int(manifest.TotalSizeBytes))
	for index, chunk := range manifest.Chunks {
		if chunk == nil || chunk.Sequence != uint32(index) || chunk.SizeBytes != uint64(len(chunk.GetInlineBody())) || densitySHA(chunk.GetInlineBody()) != chunk.Sha256 {
			return nil, false
		}
		body = append(body, chunk.GetInlineBody()...)
	}
	return body, uint64(len(body)) == manifest.TotalSizeBytes && densitySHA(body) == manifest.TotalSha256
}

func densityMaximumOverlap(results []PoolResult) int {
	type event struct {
		at    time.Time
		delta int
	}
	events := make([]event, 0, len(results)*2)
	for _, result := range results {
		events = append(events, event{result.StartedAt, 1}, event{result.FinishedAt, -1})
	}
	sort.Slice(events, func(i, j int) bool {
		if events[i].at.Equal(events[j].at) {
			return events[i].delta < events[j].delta
		}
		return events[i].at.Before(events[j].at)
	})
	current, maximum := 0, 0
	for _, item := range events {
		current += item.delta
		if current > maximum {
			maximum = current
		}
	}
	return maximum
}

func densityAllowedConcurrency(concurrency int) bool {
	return concurrency == 1 || concurrency == 4 || concurrency == 8
}

func densityLowerHex(value string, size int) bool {
	if len(value) != size {
		return false
	}
	for _, char := range value {
		if !(char >= '0' && char <= '9') && !(char >= 'a' && char <= 'f') {
			return false
		}
	}
	return true
}

func densityImageIdentity(value string) bool {
	return len(value) == len("sha256:")+64 && strings.HasPrefix(value, "sha256:") && densityLowerHex(strings.TrimPrefix(value, "sha256:"), 64)
}

func densityASCII(value string) bool {
	for _, char := range []byte(value) {
		if char > 0x7f {
			return false
		}
	}
	return true
}
func densitySHA(value []byte) string {
	digest := sha256.Sum256(value)
	return hex.EncodeToString(digest[:])
}
func densityReservedPorts() int {
	loopbackPorts.Lock()
	defer loopbackPorts.Unlock()
	return len(loopbackPorts.reserved)
}
