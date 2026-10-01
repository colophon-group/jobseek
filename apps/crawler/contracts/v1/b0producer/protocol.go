// Package b0producer is the native client for the existing Go-owned producer
// control protocol. It authenticates the fixed UDS authority; callers still own
// PostgreSQL/host cutover evidence and durable mutation intent.
package b0producer

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"regexp"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

const (
	Protocol                = "jobseek.lightpanda.producer/v1"
	SocketPath              = "/run/jobseek-lightpanda-producer/control.sock"
	ProducerUID      uint32 = 10001
	FrameLimit       uint64 = 256 * 1024
	LifetimeCapacity int64  = 2048
	maxInteger       int64  = 9999999999999
)

var (
	ErrConfiguration = errors.New("producer client configuration rejected")
	ErrAuthority     = errors.New("producer authority rejected")
	ErrProtocol      = errors.New("producer protocol rejected")
	ErrUnavailable   = errors.New("producer authority unavailable")
	digestPattern    = regexp.MustCompile(`^[0-9a-f]{64}$`)
	idPattern        = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
	domainPattern    = regexp.MustCompile(`^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$`)
)

// Request and Response preserve the installed producer's exact existing wire
// fields. The server aliases these types; its operation authority remains local.
type Request struct {
	Version             string            `json:"version"`
	Operation           string            `json:"operation"`
	Cohort              string            `json:"cohort"`
	Domain              string            `json:"domain"`
	PostingID           string            `json:"posting_id"`
	NextScrapeAtMS      int64             `json:"next_scrape_at_ms"`
	Config              map[string]string `json:"config"`
	Browser             bool              `json:"browser"`
	FirstTime           bool              `json:"first_time"`
	OperatorTransfer    bool              `json:"operator_transfer"`
	ExpectedDigest      string            `json:"expected_digest"`
	LegacyScheduleScore string            `json:"legacy_schedule_score,omitempty"`
}

type Response struct {
	Version               string   `json:"version"`
	Outcome               string   `json:"outcome"`
	Reason                string   `json:"reason"`
	PreparationDigest     string   `json:"preparation_digest"`
	PayloadSHA256         string   `json:"payload_sha256"`
	ExistingState         string   `json:"existing_state"`
	ExistingPayloadSHA256 string   `json:"existing_payload_sha256"`
	Activated             bool     `json:"activated"`
	Cohort                string   `json:"cohort"`
	BoardSlugs            []string `json:"board_slugs"`
	LifetimeOccupancy     int64    `json:"lifetime_occupancy"`
	LifetimeCapacity      int64    `json:"lifetime_capacity"`
	LifetimeHeadroom      int64    `json:"lifetime_headroom"`
}

// CapacityError is a validated producer decision; it carries bounded counters
// rather than an arbitrary remote error string.
type CapacityError struct {
	Reason              string
	Occupancy, Capacity int64
}

func (*CapacityError) Error() string { return "producer capacity refused" }

func canonical(value any) ([]byte, error) {
	// The shared task codec freezes exact ASCII escaping and sorted fields.
	return b0task.CanonicalJSON(value, true)
}

func EncodeRequest(r Request) ([]byte, error) {
	if r.Version != Protocol || r.Config == nil || r.NextScrapeAtMS < 0 || r.NextScrapeAtMS > maxInteger {
		return nil, ErrConfiguration
	}
	if r.Operation == "manifest" {
		if !validCohort(r.Cohort) || !r.OperatorTransfer || r.Domain != "" || r.PostingID != "" || r.NextScrapeAtMS != 0 || len(r.Config) != 0 || r.Browser || r.FirstTime || r.ExpectedDigest != "" || r.LegacyScheduleScore != "" {
			return nil, ErrConfiguration
		}
	} else {
		if r.Cohort != "" || !idPattern.MatchString(r.PostingID) || !domainPattern.MatchString(r.Domain) || len(r.Domain) > 253 || len(r.LegacyScheduleScore) > 32 {
			return nil, ErrConfiguration
		}
		switch r.Operation {
		case "prepare":
			if !r.OperatorTransfer || r.ExpectedDigest != "" {
				return nil, ErrConfiguration
			}
		case "activate":
			if !r.OperatorTransfer || !digestPattern.MatchString(r.ExpectedDigest) {
				return nil, ErrConfiguration
			}
		case "enqueue":
			if r.OperatorTransfer || r.ExpectedDigest != "" || r.LegacyScheduleScore != "" {
				return nil, ErrConfiguration
			}
		default:
			return nil, ErrConfiguration
		}
	}
	for key := range r.Config {
		if !idPattern.MatchString(key) {
			return nil, ErrConfiguration
		}
	}
	body, err := canonical(r)
	if err != nil || len(body) == 0 || uint64(len(body))+framing.UvarintSize(uint64(len(body))) > FrameLimit {
		return nil, ErrConfiguration
	}
	return body, nil
}

func validCohort(s string) bool {
	return s == "c1" || s == "c2" || s == "c3" || s == "c4" || s == "cdom"
}

// DecodeResponse refuses unknown/missing/duplicate fields, noncanonical bytes,
// malformed counts and inconsistent decisions. Errors contain no remote inputs.
func DecodeResponse(body []byte) (Response, error) {
	var r Response
	if len(body) == 0 || uint64(len(body))+framing.UvarintSize(uint64(len(body))) > FrameLimit {
		return r, ErrProtocol
	}
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&r) != nil || d.Decode(new(any)) != io.EOF {
		return Response{}, ErrProtocol
	}
	wanted, err := canonical(r)
	if err != nil || !bytes.Equal(body, wanted) || r.Version != Protocol || r.BoardSlugs == nil || r.LifetimeCapacity != LifetimeCapacity || r.LifetimeOccupancy < 0 || r.LifetimeOccupancy > LifetimeCapacity || r.LifetimeHeadroom != LifetimeCapacity-r.LifetimeOccupancy {
		return Response{}, ErrProtocol
	}
	for i, slug := range r.BoardSlugs {
		if !idPattern.MatchString(slug) || i > 0 && r.BoardSlugs[i-1] >= slug {
			return Response{}, ErrProtocol
		}
	}
	emptyTask := r.PreparationDigest == "" && r.PayloadSHA256 == "" && r.ExistingState == "" && r.ExistingPayloadSHA256 == ""
	emptyManifest := r.Cohort == "" && len(r.BoardSlugs) == 0
	switch r.Outcome {
	case "error":
		if !emptyTask || !emptyManifest || r.Activated || (r.Reason != "request_invalid" && r.Reason != "authority_lost" && r.Reason != "digest_mismatch") {
			return Response{}, ErrProtocol
		}
		return Response{}, ErrAuthority
	case "capacity":
		if !emptyTask || !emptyManifest || r.Activated || !(r.Reason == "namespace_full" && r.LifetimeHeadroom == 0 || r.Reason == "pilot_occupancy_limit" && r.LifetimeHeadroom > 0 && r.LifetimeHeadroom <= 448) {
			return Response{}, ErrProtocol
		}
		return Response{}, &CapacityError{r.Reason, r.LifetimeOccupancy, r.LifetimeCapacity}
	case "legacy":
		if r.Reason != "literal_legacy" || !emptyTask || !emptyManifest || r.Activated {
			return Response{}, ErrProtocol
		}
	case "manifest":
		if r.Reason != "manifest" || !validCohort(r.Cohort) || len(r.BoardSlugs) == 0 || !emptyTask || r.Activated {
			return Response{}, ErrProtocol
		}
	case "prepared", "activated":
		if !emptyManifest || !digestPattern.MatchString(r.PreparationDigest) || !digestPattern.MatchString(r.PayloadSHA256) || (r.ExistingState == "") != (r.ExistingPayloadSHA256 == "") || r.ExistingPayloadSHA256 != "" && !digestPattern.MatchString(r.ExistingPayloadSHA256) {
			return Response{}, ErrProtocol
		}
		if r.ExistingState != "" && r.ExistingState != "ready" && r.ExistingState != "inflight" && r.ExistingState != "dead" && r.ExistingState != "terminal" {
			return Response{}, ErrProtocol
		}
		if r.Outcome == "prepared" {
			if r.Reason != "prepared" || r.Activated {
				return Response{}, ErrProtocol
			}
		} else if r.Reason != "activated" && r.Reason != "reactivated" && r.Reason != "already_activated" || r.Activated != (r.Reason == "activated" || r.Reason == "reactivated") {
			return Response{}, ErrProtocol
		}
	default:
		return Response{}, ErrProtocol
	}
	return r, nil
}

// CanonicalResponse supports wire conformance checks against actual server
// replies. It grants no operation or producer authority.
func CanonicalResponse(r Response) ([]byte, error) { return canonical(r) }

// Task binds exact millisecond scheduling and optional decimal source-score CAS.
// The producer remains the owner of parser assignment, payload and activation.
type Task struct {
	Domain, PostingID   string
	NextScrapeAtMS      int64
	Config              map[string]string
	Browser, FirstTime  bool
	LegacyScheduleScore string
}

func taskRequest(t Task, operation, digest string, transfer bool) Request {
	return Request{Version: Protocol, Operation: operation, Domain: t.Domain, PostingID: t.PostingID, NextScrapeAtMS: t.NextScrapeAtMS, Config: t.Config, Browser: t.Browser, FirstTime: t.FirstTime, OperatorTransfer: transfer, ExpectedDigest: digest, LegacyScheduleScore: t.LegacyScheduleScore}
}
