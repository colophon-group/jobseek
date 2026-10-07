// Package feedsession binds a bounded, affine raw-feed traversal to the
// existing pinned Lightpanda reservation. Pages are provisional until cleanup.
package feedsession

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

const Protocol = "jobseek.lightpanda.feed-session/v1"
const RequestLimit = 16 << 10
const CommandLimit = 1024
const BodyLimit = 2_000_000
const MaxDurationMS = 600_000

var ErrProtocol = errors.New("invalid feed browser conversation")
var digest = regexp.MustCompile(`^[0-9a-f]{64}$`)
var parameter = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_.-]{0,63}$`)

type Request struct {
	Protocol            string  `json:"protocol"`
	RequestID           string  `json:"request_id"`
	ConfigFingerprint   string  `json:"config_fingerprint"`
	FeedURL             string  `json:"feed_url"`
	PageParameter       string  `json:"page_parameter"`
	Start               int     `json:"start"`
	Increment           int     `json:"increment"`
	MaxPages            int     `json:"max_pages"`
	Wait                string  `json:"wait"`
	WaitFallback        *string `json:"wait_fallback,omitempty"`
	NavigationTimeoutMS uint64  `json:"navigation_timeout_ms"`
	TimeoutMS           uint64  `json:"timeout_ms"`
}

func (r Request) Valid() bool {
	if r.Protocol != Protocol || !digest.MatchString(r.RequestID) || !digest.MatchString(r.ConfigFingerprint) || len(r.FeedURL) > 8192 || strings.ContainsAny(r.FeedURL, "\x00\r\n\t ") || r.Start < 1 || r.Start > 10_000_000 || r.Increment < 1 || r.Increment > 10_000_000 || r.MaxPages < 1 || r.MaxPages > 50001 || int64(r.Start)+int64(r.MaxPages-1)*int64(r.Increment) > 10_000_000 || r.NavigationTimeoutMS < 1 || r.NavigationTimeoutMS > 120000 || r.TimeoutMS < 1 || r.TimeoutMS > MaxDurationMS {
		return false
	}
	if r.PageParameter != "" && !parameter.MatchString(r.PageParameter) || r.PageParameter == "" && (r.Start != 1 || r.Increment != 1 || r.MaxPages != 1) {
		return false
	}
	if r.WaitFallback != nil && (*r.WaitFallback == r.Wait || *r.WaitFallback != "commit" && *r.WaitFallback != "domcontentloaded" && *r.WaitFallback != "load" && *r.WaitFallback != "networkidle") {
		return false
	}
	if r.Wait != "commit" && r.Wait != "domcontentloaded" && r.Wait != "load" && r.Wait != "networkidle" {
		return false
	}
	u, e := url.Parse(r.FeedURL)
	return e == nil && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && u.Fragment == "" && u.Opaque == ""
}
func (r Request) PageURL(page int) (string, error) {
	if !r.Valid() || page < r.Start || (page-r.Start)%r.Increment != 0 || (page-r.Start)/r.Increment >= r.MaxPages {
		return "", ErrProtocol
	}
	if r.PageParameter == "" {
		return r.FeedURL, nil
	}
	return PageURL(r.FeedURL, page, r.PageParameter)
}

// Preserve Python parse_qs/urlencode ordering and first nonempty values.
func PageURL(feed string, page int, param string) (string, error) {
	u, e := url.Parse(feed)
	if e != nil {
		return "", ErrProtocol
	}
	keys := []string{}
	values := map[string]string{}
	for _, entry := range strings.Split(u.RawQuery, "&") {
		key, value, ok := strings.Cut(entry, "=")
		if !ok || value == "" {
			continue
		}
		key, e = url.QueryUnescape(key)
		if e != nil {
			return "", ErrProtocol
		}
		value, e = url.QueryUnescape(value)
		if e != nil {
			return "", ErrProtocol
		}
		if _, exists := values[key]; !exists {
			keys = append(keys, key)
			values[key] = value
		}
	}
	if _, exists := values[param]; !exists {
		keys = append(keys, param)
	}
	values[param] = strconv.Itoa(page)
	encoded := make([]string, 0, len(keys))
	for _, key := range keys {
		encoded = append(encoded, url.QueryEscape(key)+"="+url.QueryEscape(values[key]))
	}
	u.RawQuery = strings.Join(encoded, "&")
	return u.String(), nil
}

type Command struct {
	Sequence uint64 `json:"sequence"`
	Page     *int   `json:"page,omitempty"`
	Finish   *bool  `json:"finish,omitempty"`
}

func (c Command) Valid() bool {
	return c.Sequence > 0 && c.Sequence <= 50002 && (c.Page != nil) != (c.Finish != nil) && (c.Page == nil || *c.Page >= 1 && *c.Page <= 10000000)
}

type Control struct {
	RequestID string  `json:"request_id"`
	Sequence  uint64  `json:"sequence"`
	Ready     bool    `json:"ready,omitempty"`
	Closed    *Closed `json:"closed,omitempty"`
}
type Closed struct {
	Success bool   `json:"success"`
	Reason  string `json:"reason"`
}

func (c Control) Valid() bool {
	return digest.MatchString(c.RequestID) && c.Sequence <= 50002 && (c.Ready && c.Sequence == 0 && c.Closed == nil || !c.Ready && c.Sequence > 0 && c.Closed != nil && (c.Closed.Reason == "completed" && c.Closed.Success || c.Closed.Reason == "aborted" && !c.Closed.Success || c.Closed.Reason == "failed" && !c.Closed.Success))
}
func Decode(raw []byte, limit int, out any) error {
	if len(raw) == 0 || len(raw) > limit {
		return ErrProtocol
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(out) != nil || d.Decode(new(any)) != io.EOF {
		return ErrProtocol
	}
	return nil
}
