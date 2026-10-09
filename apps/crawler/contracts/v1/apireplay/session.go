// Package apireplay binds one complete API inventory to a pinned browser
// reservation. The sole response follows browser and HTTP session cleanup.
package apireplay

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"regexp"
)

const Protocol = "jobseek.lightpanda.api-replay/v1"
const RequestLimit = 128 << 10
const ResponseLimit = 8 << 20
const MaxDurationMS = 600000

var ErrProtocol = errors.New("invalid API replay request or response")
var digest = regexp.MustCompile(`^[0-9a-f]{64}$`)

type Request struct {
	Protocol          string          `json:"protocol"`
	RequestID         string          `json:"request_id"`
	ConfigFingerprint string          `json:"config_fingerprint"`
	BoardURL          string          `json:"board_url"`
	Metadata          json.RawMessage `json:"metadata"`
	Provider          string          `json:"provider,omitempty"`
	TimeoutMS         uint64          `json:"timeout_ms"`
}

func (Request) String() string   { return "API replay request" }
func (Request) GoString() string { return "API replay request" }
func (r Request) Valid() bool {
	if r.Provider != "" && r.Provider != "darwinbox" && r.Provider != "bytedance" && r.Provider != "brassring" && r.Provider != "accenture" {
		return false
	}
	u, err := url.Parse(r.BoardURL)
	return r.Protocol == Protocol && digest.MatchString(r.RequestID) && digest.MatchString(r.ConfigFingerprint) && len(r.BoardURL) <= 8192 && err == nil && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && u.Opaque == "" && len(r.Metadata) > 1 && len(r.Metadata) <= 64<<10 && json.Valid(r.Metadata) && bytes.HasPrefix(bytes.TrimSpace(r.Metadata), []byte("{")) && r.TimeoutMS > 0 && r.TimeoutMS <= MaxDurationMS
}

type Reservation struct {
	URL       string  `json:"url"`
	Source    string  `json:"source"`
	PolicyURL *string `json:"policy_url,omitempty"`
}
type Response struct {
	Protocol          string          `json:"protocol"`
	RequestID         string          `json:"request_id"`
	ConfigFingerprint string          `json:"config_fingerprint"`
	Outcome           string          `json:"outcome"`
	Inventory         json.RawMessage `json:"inventory,omitempty"`
	Reservation       *Reservation    `json:"reservation,omitempty"`
	FailureURL        string          `json:"failure_url,omitempty"`
	FailureStatus     int             `json:"failure_status,omitempty"`
}

func (r Response) Valid() bool {
	if r.Protocol != Protocol || !digest.MatchString(r.RequestID) || !digest.MatchString(r.ConfigFingerprint) {
		return false
	}
	switch r.Outcome {
	case "success", "partial":
		return r.FailureURL == "" && r.FailureStatus == 0 && r.Reservation == nil && len(r.Inventory) > 1 && len(r.Inventory) <= ResponseLimit-1024 && json.Valid(r.Inventory) && bytes.HasPrefix(bytes.TrimSpace(r.Inventory), []byte("{"))
	case "provider_gone":
		u, e := url.Parse(r.FailureURL)
		return e == nil && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && len(r.FailureURL) <= 8192 && (r.FailureStatus == 404 || r.FailureStatus == 410) && len(r.Inventory) == 0 && r.Reservation == nil
	case "publisher_reserved":
		return r.FailureURL == "" && r.FailureStatus == 0 && len(r.Inventory) == 0 && r.Reservation != nil && len(r.Reservation.URL) > 0 && len(r.Reservation.URL) <= 8192 && (r.Reservation.Source == "header" || r.Reservation.Source == "meta") && (r.Reservation.PolicyURL == nil || len(*r.Reservation.PolicyURL) <= 8192)
	case "invalid_config", "failed":
		return r.FailureURL == "" && r.FailureStatus == 0 && len(r.Inventory) == 0 && r.Reservation == nil
	}
	return false
}
func Decode(body []byte, limit int, out any) error {
	if len(body) == 0 || len(body) > limit {
		return ErrProtocol
	}
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(out) != nil || d.Decode(new(any)) != io.EOF {
		return ErrProtocol
	}
	return nil
}
