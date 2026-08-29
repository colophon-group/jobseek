package harness

import (
	"encoding/json"
	"sort"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

const (
	ContractVersion = "crawler.runtime/v1"
	OutputFormat    = "jobseek.lightpanda-probe/v1"
)

type Limits struct {
	MaxRequests      uint64
	MaxResponseBytes uint64
}

type LedgerEntry struct {
	Sequence      uint64 `json:"sequence"`
	Method        string `json:"method"`
	Path          string `json:"path"`
	ResourceType  string `json:"resource_type"`
	Decision      string `json:"decision"`
	Status        int64  `json:"status,omitempty"`
	ResponseBytes uint64 `json:"response_bytes,omitempty"`
	Reason        string `json:"reason,omitempty"`
}

type Cleanup struct {
	SessionClosed bool   `json:"session_closed"`
	TargetsBefore int    `json:"targets_before"`
	TargetsAfter  int    `json:"targets_after"`
	Outcome       string `json:"outcome"`
}

type Envelope struct {
	Format        string          `json:"format"`
	FixtureID     string          `json:"fixture_id"`
	PlanSHA256    string          `json:"plan_sha256"`
	Result        json.RawMessage `json:"result"`
	Ledger        []LedgerEntry   `json:"ledger"`
	RequestCount  uint64          `json:"request_count"`
	ResponseBytes uint64          `json:"response_bytes"`
	Cleanup       Cleanup         `json:"cleanup"`
}

func SortLedger(entries []LedgerEntry) {
	sort.Slice(entries, func(i, j int) bool {
		a, b := entries[i], entries[j]
		if a.Path != b.Path {
			return a.Path < b.Path
		}
		if a.Method != b.Method {
			return a.Method < b.Method
		}
		if a.ResourceType != b.ResourceType {
			return a.ResourceType < b.ResourceType
		}
		if a.Decision != b.Decision {
			return a.Decision < b.Decision
		}
		if a.Status != b.Status {
			return a.Status < b.Status
		}
		if a.ResponseBytes != b.ResponseBytes {
			return a.ResponseBytes < b.ResponseBytes
		}
		return a.Reason < b.Reason
	})
	for index := range entries {
		entries[index].Sequence = uint64(index + 1)
	}
}

func newResult(outcome interface{}) *runtimev1.BrowserResult {
	result := &runtimev1.BrowserResult{
		ContractVersion: ContractVersion,
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
	}
	switch value := outcome.(type) {
	case *runtimev1.BrowserSuccess:
		result.Outcome = &runtimev1.BrowserResult_Success{Success: value}
	case *runtimev1.BrowserFailure:
		result.Outcome = &runtimev1.BrowserResult_Error{Error: value}
	case *runtimev1.BrowserUnsupported:
		result.Outcome = &runtimev1.BrowserResult_Unsupported{Unsupported: value}
	default:
		panic("invalid browser result outcome")
	}
	return result
}

func Unsupported(capabilities []runtimev1.BrowserCapability) *runtimev1.BrowserResult {
	capabilities = append([]runtimev1.BrowserCapability(nil), capabilities...)
	sort.Slice(capabilities, func(i, j int) bool { return capabilities[i] < capabilities[j] })
	return newResult(&runtimev1.BrowserUnsupported{Capabilities: capabilities})
}

func Failure(code runtimev1.ErrorCode, disposition runtimev1.ErrorDisposition, message string) *runtimev1.BrowserResult {
	return newResult(&runtimev1.BrowserFailure{Error: &runtimev1.RuntimeError{
		Code:        code,
		Disposition: disposition,
		Message:     message,
	}})
}
