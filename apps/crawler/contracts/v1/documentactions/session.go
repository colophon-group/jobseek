// Package documentactions binds a sequential browser action pipeline to one
// fresh render reservation. It returns only the existing typed browser result.
package documentactions

import (
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
)

const Protocol = "jobseek.lightpanda.document-actions/v1"
const RequestLimit = 128 << 10
const ResponseLimit = 2 << 20
const MaxActions = 32
const MaxBudget = 300 * time.Second

var ErrActions = errors.New("unsupported browser action pipeline")
var digest = regexp.MustCompile(`^[0-9a-f]{64}$`)

type Action struct {
	Kind         string  `json:"action"`
	Script       string  `json:"script,omitempty"`
	Milliseconds float64 `json:"ms,omitempty"`
	TimeoutMS    uint64  `json:"timeout_ms"`
	Required     bool    `json:"required,omitempty"`
}

func (Action) String() string   { return "browser action" }
func (Action) GoString() string { return "browser action" }
func Valid(actions []Action) bool {
	if len(actions) == 0 || len(actions) > MaxActions {
		return false
	}
	var budget uint64
	for _, a := range actions {
		if a.TimeoutMS == 0 || a.TimeoutMS > 120000 {
			return false
		}
		budget += a.TimeoutMS
		switch a.Kind {
		case "wait":
			if a.Script != "" || math.IsNaN(a.Milliseconds) || math.IsInf(a.Milliseconds, 0) || a.Milliseconds < 0 || a.Milliseconds > 120000 {
				return false
			}
		case "evaluate":
			if len(a.Script) == 0 || len(a.Script) > 16384 || a.Milliseconds != 0 {
				return false
			}
		default:
			return false
		}
	}
	body, err := json.Marshal(actions)
	return err == nil && len(body) <= 32768 && budget <= uint64(MaxBudget/time.Millisecond)
}
func Budget(actions []Action) time.Duration {
	var n uint64
	for _, a := range actions {
		n += a.TimeoutMS
	}
	return time.Duration(n) * time.Millisecond
}

// Parse preserves Python's sequential order, ten-second action timeout and
// optional failure default. Unsupported interactions retain their current owner.
func Parse(raw any) ([]Action, error) {
	if raw == nil {
		return nil, nil
	}
	values, ok := raw.([]any)
	if !ok || len(values) > MaxActions {
		return nil, ErrActions
	}
	if len(values) == 0 {
		return nil, nil
	}
	result := make([]Action, 0, len(values))
	for _, v := range values {
		m, ok := v.(map[string]any)
		if !ok {
			return nil, ErrActions
		}
		a := Action{TimeoutMS: 10000}
		a.Kind, _ = m["action"].(string)
		for k := range m {
			if k != "action" && k != "required" && k != "timeout" && !(k == "script" && a.Kind == "evaluate") && !(k == "ms" && a.Kind == "wait") {
				return nil, ErrActions
			}
		}
		if v, exists := m["required"]; exists {
			a.Required, ok = v.(bool)
			if !ok {
				return nil, ErrActions
			}
		}
		if v, exists := m["timeout"]; exists {
			n, yes := v.(float64)
			if !yes || math.IsNaN(n) || math.IsInf(n, 0) || n <= 0 || n > 120 || n*1000 != math.Trunc(n*1000) {
				return nil, ErrActions
			}
			a.TimeoutMS = uint64(n * 1000)
		}
		switch a.Kind {
		case "wait":
			a.Milliseconds = 1000
			if v, exists := m["ms"]; exists {
				a.Milliseconds, ok = v.(float64)
				if !ok {
					return nil, ErrActions
				}
			}
		case "evaluate":
			a.Script, ok = m["script"].(string)
			if !ok {
				return nil, ErrActions
			}
		default:
			return nil, ErrActions
		}
		result = append(result, a)
	}
	if !Valid(result) {
		return nil, ErrActions
	}
	return result, nil
}

type Request struct {
	Protocol          string   `json:"protocol"`
	RequestID         string   `json:"request_id"`
	ConfigFingerprint string   `json:"config_fingerprint"`
	Input             []byte   `json:"input"`
	Actions           []Action `json:"actions"`
}

func (Request) String() string   { return "document action request" }
func (Request) GoString() string { return "document action request" }
func (r Request) Valid() bool {
	return r.Protocol == Protocol && digest.MatchString(r.RequestID) && digest.MatchString(r.ConfigFingerprint) && len(r.Input) > 0 && len(r.Input) <= 65536 && Valid(r.Actions)
}

type Response struct {
	Protocol          string `json:"protocol"`
	RequestID         string `json:"request_id"`
	ConfigFingerprint string `json:"config_fingerprint"`
	Result            []byte `json:"result"`
}

func (r Response) Valid() bool {
	return r.Protocol == Protocol && digest.MatchString(r.RequestID) && digest.MatchString(r.ConfigFingerprint) && len(r.Result) > 0 && len(r.Result) <= 1049600
}
func Decode(body []byte, limit int, out any) error { return replay.Decode(body, limit, out) }

// MarshalBounded checks the full wire ceiling as well as individual fields.
func MarshalBounded(r Request) ([]byte, error) {
	if !r.Valid() {
		return nil, ErrActions
	}
	b, e := json.Marshal(r)
	if e != nil || len(b) > RequestLimit {
		return nil, ErrActions
	}
	return b, nil
}
