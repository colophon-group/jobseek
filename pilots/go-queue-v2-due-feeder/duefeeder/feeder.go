package duefeeder

import (
	"context"
	"errors"
	"net/url"
	"strings"
	"sync"

	queuev2 "github.com/colophon-group/jobseek/apps/crawler/contracts/queue/v2/conformance/go"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-queue-v2-admission/queueworker"
)

const SitemapHTTPCohort = "sitemap-http-v1"

var (
	ErrInvalidFeeder    = errors.New("invalid due feeder")
	ErrClosed           = errors.New("due feeder closed")
	ErrInvalidReference = errors.New("invalid due reference")
	ErrResolve          = errors.New("candidate resolution failed")
	ErrUnsupported      = errors.New("candidate outside supported sitemap cohort")
)

// ResolvedCandidate makes unsupported and browser-dependent profiles explicit
// instead of inferring them from an incomplete runtime manifest.
type ResolvedCandidate struct {
	Candidate       queueworker.Candidate
	Cohort          string
	BrowserRequired bool
}

// CandidateResolver must look up exactly (TaskID, ConfigRevision). It must not
// fall forward to a mutable latest revision.
type CandidateResolver interface {
	Resolve(context.Context, Reference) (ResolvedCandidate, error)
}

type CandidateResolverFunc func(context.Context, Reference) (ResolvedCandidate, error)

func (f CandidateResolverFunc) Resolve(
	ctx context.Context,
	reference Reference,
) (ResolvedCandidate, error) {
	return f(ctx, reference)
}

// CandidateSink is implemented by queueworker.Runner. Close is part of the
// boundary so closing a feeder also closes the runner's claim gate.
type CandidateSink interface {
	Submit(context.Context, queueworker.Candidate) error
	Close()
}

var _ CandidateSink = (*queueworker.Runner)(nil)

// Report describes only local one-shot progress. It is not a queue cursor or
// lifecycle authority.
type Report struct {
	Observed  int
	Resolved  int
	Submitted int
}

// ItemError identifies a bounded page position without echoing task IDs,
// URLs, resolver errors, or other potentially sensitive configuration.
type ItemError struct {
	Index int
	Stage string
	Err   error
}

func (e *ItemError) Error() string { return "due feeder " + e.Stage + " rejected page item" }
func (e *ItemError) Unwrap() error { return e.Err }

// Feeder owns no polling or worker goroutine. It tracks active calls only to
// cancel them and synchronize the Runner close gate.
type Feeder struct {
	source   DueSource
	resolver CandidateResolver
	sink     CandidateSink

	stateMu sync.Mutex
	closed  bool
	nextID  uint64
	active  map[uint64]context.CancelCauseFunc

	submitGate sync.RWMutex
	closeOnce  sync.Once
}

func New(source DueSource, resolver CandidateResolver, sink CandidateSink) (*Feeder, error) {
	if source == nil || resolver == nil || sink == nil {
		return nil, ErrInvalidFeeder
	}
	return &Feeder{
		source: source, resolver: resolver, sink: sink,
		active: make(map[uint64]context.CancelCauseFunc),
	}, nil
}

// FeedOnce resolves the entire bounded page before the first submission. A
// malformed or unsupported item therefore cannot partially admit its page.
func (f *Feeder) FeedOnce(ctx context.Context, limit int) (Report, error) {
	if err := validateLimit(ctx, limit); err != nil {
		return Report{}, err
	}
	if f == nil {
		return Report{}, ErrInvalidFeeder
	}
	feedCtx, finish, err := f.begin(ctx)
	if err != nil {
		return Report{}, err
	}
	defer finish()

	references, err := f.source.ListDue(feedCtx, limit)
	if err != nil {
		if feedCtx.Err() != nil {
			return Report{}, context.Cause(feedCtx)
		}
		if errors.Is(err, ErrInvalidSnapshot) || errors.Is(err, ErrInvalidLimit) {
			return Report{}, err
		}
		return Report{}, ErrSourceRead
	}
	if feedCtx.Err() != nil {
		return Report{}, context.Cause(feedCtx)
	}
	report := Report{Observed: len(references)}
	if len(references) > limit {
		return report, ErrInvalidSnapshot
	}
	for index, reference := range references {
		if err := validateReference(reference); err != nil {
			return report, &ItemError{Index: index, Stage: "source", Err: err}
		}
	}

	resolved := make([]queueworker.Candidate, len(references))
	for index, reference := range references {
		if err := feedCtx.Err(); err != nil {
			return report, context.Cause(feedCtx)
		}
		candidate, resolveErr := f.resolver.Resolve(feedCtx, reference)
		if resolveErr != nil {
			if err := feedCtx.Err(); err != nil {
				return report, context.Cause(feedCtx)
			}
			return report, &ItemError{Index: index, Stage: "resolve", Err: ErrResolve}
		}
		if err := validateResolved(reference, candidate); err != nil {
			return report, &ItemError{Index: index, Stage: "resolve", Err: err}
		}
		resolved[index] = candidate.Candidate
		report.Resolved++
	}

	for index, candidate := range resolved {
		f.submitGate.RLock()
		if f.isClosed() {
			f.submitGate.RUnlock()
			return report, ErrClosed
		}
		submitErr := f.sink.Submit(feedCtx, candidate)
		f.submitGate.RUnlock()
		if submitErr != nil {
			if err := feedCtx.Err(); err != nil {
				return report, context.Cause(feedCtx)
			}
			return report, &ItemError{Index: index, Stage: "submit", Err: submitErr}
		}
		report.Submitted++
	}
	return report, nil
}

func (f *Feeder) begin(parent context.Context) (context.Context, func(), error) {
	f.stateMu.Lock()
	defer f.stateMu.Unlock()
	if f.closed {
		return nil, nil, ErrClosed
	}
	ctx, cancel := context.WithCancelCause(parent)
	f.nextID++
	id := f.nextID
	f.active[id] = cancel
	return ctx, func() {
		cancel(nil)
		f.stateMu.Lock()
		delete(f.active, id)
		f.stateMu.Unlock()
	}, nil
}

func (f *Feeder) isClosed() bool {
	f.stateMu.Lock()
	defer f.stateMu.Unlock()
	return f.closed
}

// Close cancels active source/resolver/Submit calls, closes the Runner claim
// gate, and waits for an in-progress Submit call to return. It is idempotent.
func (f *Feeder) Close() {
	if f == nil {
		return
	}
	f.closeOnce.Do(func() {
		f.stateMu.Lock()
		f.closed = true
		cancels := make([]context.CancelCauseFunc, 0, len(f.active))
		for _, cancel := range f.active {
			cancels = append(cancels, cancel)
		}
		f.stateMu.Unlock()
		for _, cancel := range cancels {
			cancel(ErrClosed)
		}
		f.sink.Close()
		f.submitGate.Lock()
		f.submitGate.Unlock()
	})
}

func validateReference(reference Reference) error {
	if strings.TrimSpace(reference.TaskID) == "" || len(reference.TaskID) > maxTaskIDBytes ||
		reference.ConfigRevision < 1 ||
		reference.ConfigRevision > queuev2.RedisCandidateMaxInteger {
		return ErrInvalidReference
	}
	return nil
}

func validateResolved(reference Reference, resolved ResolvedCandidate) error {
	candidate := resolved.Candidate
	if resolved.Cohort != SitemapHTTPCohort || resolved.BrowserRequired {
		return ErrUnsupported
	}
	if candidate.Job.ID != reference.TaskID || candidate.ConfigRevision != reference.ConfigRevision {
		return ErrInvalidReference
	}
	if candidate.Job.Timeout < 0 {
		return ErrUnsupported
	}
	parsed, err := url.Parse(candidate.Job.Sitemap.SitemapURL)
	if err != nil || parsed.User != nil || parsed.Host == "" || parsed.Hostname() == "" ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") {
		return ErrUnsupported
	}
	// sitemap.New is the admitted executor's authoritative configuration
	// validator. The zero client is never used: the runner is discarded.
	if _, err := sitemap.New(&boundedhttp.Client{}, candidate.Job.Sitemap); err != nil {
		return ErrUnsupported
	}
	return nil
}
