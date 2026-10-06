// Package dayforcesession defines the compiled Dayforce conversation on the
// existing pinned Lightpanda connection. The held ordinary claim owns inventory
// and writes; the browser owns one ephemeral credential lease and its cleanup.
// This conversation is separate from generic ExecutionFrame/resume semantics.
package dayforcesession

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"regexp"
	"strings"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	resourcepolicy "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/resourcepolicy"
)

const (
	Protocol      = "jobseek.lightpanda.dayforce-session/v1"
	RequestLimit  = 16 << 10
	CommandLimit  = 1024
	PageLimit     = 1 << 20
	FrameLimit    = 2 << 20
	MaxDurationMS = 600_000
)

var ErrProtocol = errors.New("invalid dayforce browser conversation")
var tenantPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var portalPattern = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]{0,126}[A-Za-z0-9])?$`)
var culturePattern = regexp.MustCompile(`^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$`)
var digestPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

type Site struct {
	JobBoardID int64    `json:"job_board_id"`
	Culture    string   `json:"culture"`
	Cultures   []string `json:"cultures"`
	Disabled   bool     `json:"disabled"`
}

type Request struct {
	Protocol          string `json:"protocol"`
	RequestID         string `json:"request_id"`
	ConfigFingerprint string `json:"config_fingerprint"`
	TargetURL         string `json:"target_url"`
	Tenant            string `json:"tenant"`
	Portal            string `json:"portal"`
	ExpectedSite      Site   `json:"expected_site"`
	OffsetOverlap     int    `json:"offset_overlap"`
	TimeoutMS         uint64 `json:"timeout_ms"`
}

func (r Request) SearchURL() string {
	return "https://jobs.dayforcehcm.com/api/geo/" + r.Tenant + "/jobposting/search"
}

func validCulture(c string) bool { return len(c) <= 32 && culturePattern.MatchString(c) }

func ValidSite(s Site) bool {
	if s.JobBoardID < 1 || !validCulture(s.Culture) || len(s.Cultures) == 0 || len(s.Cultures) > 256 {
		return false
	}
	matched := false
	for _, c := range s.Cultures {
		if !validCulture(c) {
			return false
		}
		if strings.EqualFold(c, s.Culture) {
			matched = true
		}
	}
	return matched
}

func (r Request) Valid() bool {
	if r.Protocol != Protocol || !digestPattern.MatchString(r.RequestID) || !digestPattern.MatchString(r.ConfigFingerprint) || !tenantPattern.MatchString(r.Tenant) || !portalPattern.MatchString(r.Portal) || !ValidSite(r.ExpectedSite) || r.ExpectedSite.Disabled || r.OffsetOverlap < 0 || r.OffsetOverlap >= 25 || r.TimeoutMS == 0 || r.TimeoutMS > MaxDurationMS {
		return false
	}
	switch r.Tenant {
	case "api", "app", "help", "support", "www":
		return false
	}
	u, e := url.Parse(r.TargetURL)
	if e != nil || u.Scheme != "https" || u.Host != "jobs.dayforcehcm.com" || u.User != nil || u.RawPath != "" || u.RawQuery != "" || u.Fragment != "" || u.Opaque != "" {
		return false
	}
	p := strings.Split(strings.TrimPrefix(u.Path, "/"), "/")
	if len(p) == 3 && validCulture(p[0]) {
		p = p[1:]
	}
	return len(p) == 2 && p[0] == r.Tenant && p[1] == r.Portal
}

type Command struct {
	Sequence uint64 `json:"sequence"`
	// Exactly one variant. Offset zero is present for the first search.
	Offset *int  `json:"offset,omitempty"`
	Finish *bool `json:"finish,omitempty"`
}

func (c Command) Valid() bool {
	return c.Sequence > 0 && ((c.Offset != nil && c.Finish == nil && *c.Offset >= 0 && *c.Offset < 50000) || (c.Offset == nil && c.Finish != nil))
}

type Ready struct {
	FinalURL         string                           `json:"final_url"`
	Status           int                              `json:"status"`
	Site             Site                             `json:"site"`
	Policy           *runtimev1.ResourcePolicySignals `json:"policy"`
	PublisherChecked bool                             `json:"publisher_checked"`
	Reservation      *Reservation                     `json:"reservation,omitempty"`
}

// The controller checks the complete top-level document before discarding it.
// Only a positive canonical reservation, with its provenance, is transferred.
type Reservation struct {
	Source    string  `json:"source"`
	PolicyURL *string `json:"policy_url"`
}

type Page struct {
	Offset   int                              `json:"offset"`
	FinalURL string                           `json:"final_url"`
	Status   int                              `json:"status"`
	Policy   *runtimev1.ResourcePolicySignals `json:"policy"`
	// Raw JSON bytes are encoded by encoding/json, never interpreted as a
	// second envelope. Response headers and session credentials are absent.
	Body            []byte `json:"body"`
	TransportFailed bool   `json:"transport_failed"`
}

type Closed struct {
	Success bool `json:"success"`
	// Fixed code-owned enum only; no provider error messages.
	Reason string `json:"reason"`
}

type Frame struct {
	RequestID string  `json:"request_id"`
	Sequence  uint64  `json:"sequence"`
	Ready     *Ready  `json:"ready,omitempty"`
	Page      *Page   `json:"page,omitempty"`
	Closed    *Closed `json:"closed,omitempty"`
}

func (f Frame) Valid() bool {
	n := 0
	if f.Ready != nil {
		n++
	}
	if f.Page != nil {
		n++
	}
	if f.Closed != nil {
		n++
	}
	if n != 1 || !digestPattern.MatchString(f.RequestID) {
		return false
	}
	if r := f.Ready; r != nil {
		if len(r.FinalURL) > 8192 || r.Status < 100 || r.Status > 599 || r.Policy == nil || !resourcepolicy.Valid(r.Policy) {
			return false
		}
		if reservation := r.Reservation; reservation != nil && (reservation.Source != "header" && reservation.Source != "meta" || reservation.PolicyURL != nil && len(*reservation.PolicyURL) > 8192) {
			return false
		}
	}
	if p := f.Page; p != nil && (len(p.Body) > PageLimit || p.Offset < 0 || p.Offset >= 50000 || p.Status < 0 || p.Status > 599 || len(p.FinalURL) > 8192) {
		return false
	}
	if p := f.Page; p != nil && (!resourcepolicy.Valid(p.Policy) || !p.TransportFailed && (p.Status < 100 || p.Policy == nil)) {
		return false
	}
	if p := f.Page; p != nil && p.TransportFailed && (p.Status != 0 || len(p.Body) != 0 || p.Policy != nil) {
		return false
	}
	if c := f.Closed; c != nil {
		switch c.Reason {
		case "completed", "aborted", "invalid_command", "navigation", "session", "cancelled", "resource_limit", "cleanup":
		default:
			return false
		}
		if c.Success != (c.Reason == "completed") {
			return false
		}
	}
	return true
}

// Decode rejects duplicate keys, unknown fields, trailing values and excessive
// nesting before decoding a typed envelope. No field can carry caller script.
func Decode(raw []byte, limit int, out any) error {
	if len(raw) == 0 || len(raw) > limit {
		return ErrProtocol
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	if rejectDuplicates(d, 0) != nil || d.Decode(new(any)) != io.EOF {
		return ErrProtocol
	}
	d = json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(out) != nil || d.Decode(new(any)) != io.EOF {
		return ErrProtocol
	}
	// Both peers use this exact typed Go encoding. Comparing compact bytes to
	// its canonical marshal also rejects case-folded field aliases, missing
	// required zero-valued fields and alternate envelope interpretations.
	canonical, e := json.Marshal(out)
	if e != nil {
		return ErrProtocol
	}
	var compact bytes.Buffer
	if json.Compact(&compact, raw) != nil || !bytes.Equal(canonical, compact.Bytes()) {
		return ErrProtocol
	}
	return nil
}

func rejectDuplicates(d *json.Decoder, depth int) error {
	if depth > 32 {
		return ErrProtocol
	}
	t, e := d.Token()
	if e != nil {
		return e
	}
	delimiter, ok := t.(json.Delim)
	if !ok {
		return nil
	}
	switch delimiter {
	case '{':
		seen := map[string]bool{}
		for d.More() {
			k, e := d.Token()
			s, ok := k.(string)
			if e != nil || !ok || seen[s] {
				return ErrProtocol
			}
			seen[s] = true
			if rejectDuplicates(d, depth+1) != nil {
				return ErrProtocol
			}
		}
	case '[':
		for d.More() {
			if rejectDuplicates(d, depth+1) != nil {
				return ErrProtocol
			}
		}
	default:
		return ErrProtocol
	}
	end, e := d.Token()
	if e != nil || (delimiter == '{' && end != json.Delim('}')) || (delimiter == '[' && end != json.Delim(']')) {
		return ErrProtocol
	}
	return nil
}
