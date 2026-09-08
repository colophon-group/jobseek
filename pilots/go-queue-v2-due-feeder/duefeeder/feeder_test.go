package duefeeder

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	queuev2 "github.com/colophon-group/jobseek/apps/crawler/contracts/queue/v2/conformance/go"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
	"github.com/colophon-group/jobseek/pilots/go-queue-v2-admission/queueworker"
	"github.com/redis/go-redis/v9"
)

type sourceFunc func(context.Context, int) ([]Reference, error)

func (f sourceFunc) ListDue(ctx context.Context, limit int) ([]Reference, error) {
	return f(ctx, limit)
}

type fakeSink struct {
	mu        sync.Mutex
	submitted []queueworker.Candidate
	closed    int
	submit    func(context.Context, queueworker.Candidate) error
}

func (s *fakeSink) Submit(ctx context.Context, candidate queueworker.Candidate) error {
	if s.submit != nil {
		if err := s.submit(ctx, candidate); err != nil {
			return err
		}
	}
	s.mu.Lock()
	s.submitted = append(s.submitted, candidate)
	s.mu.Unlock()
	return nil
}

func (s *fakeSink) Close() {
	s.mu.Lock()
	s.closed++
	s.mu.Unlock()
}

func (s *fakeSink) snapshot() ([]queueworker.Candidate, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]queueworker.Candidate(nil), s.submitted...), s.closed
}

func candidateFor(reference Reference, rawURL string) ResolvedCandidate {
	return ResolvedCandidate{
		Cohort: SitemapHTTPCohort,
		Candidate: queueworker.Candidate{
			Job: worker.Job{
				ID: reference.TaskID,
				Sitemap: sitemap.Config{
					SitemapURL: rawURL, MaxURLs: 100, MaxIndexChildren: 10,
				},
			},
			ConfigRevision: reference.ConfigRevision,
		},
	}
}

func TestFeedOnceResolvesWholePageThenSubmitsInOrder(t *testing.T) {
	references := []Reference{
		{TaskID: "b", ConfigRevision: 2},
		{TaskID: "a", ConfigRevision: 1},
		{TaskID: "a", ConfigRevision: 1},
	}
	var eventsMu sync.Mutex
	var events []string
	source := sourceFunc(func(_ context.Context, limit int) ([]Reference, error) {
		if limit != len(references) {
			t.Fatalf("limit=%d", limit)
		}
		return append([]Reference(nil), references...), nil
	})
	resolver := CandidateResolverFunc(func(_ context.Context, reference Reference) (ResolvedCandidate, error) {
		eventsMu.Lock()
		events = append(events, "resolve:"+reference.TaskID)
		eventsMu.Unlock()
		return candidateFor(reference, "https://"+reference.TaskID+".example/sitemap.xml"), nil
	})
	sink := &fakeSink{}
	sink.submit = func(_ context.Context, candidate queueworker.Candidate) error {
		eventsMu.Lock()
		events = append(events, "submit:"+candidate.Job.ID)
		eventsMu.Unlock()
		return nil
	}
	feeder, err := New(source, resolver, sink)
	if err != nil {
		t.Fatal(err)
	}
	report, err := feeder.FeedOnce(context.Background(), len(references))
	if err != nil {
		t.Fatal(err)
	}
	if report != (Report{Observed: 3, Resolved: 3, Submitted: 3}) {
		t.Fatalf("report=%+v", report)
	}
	eventsMu.Lock()
	wantEvents := []string{"resolve:b", "resolve:a", "resolve:a", "submit:b", "submit:a", "submit:a"}
	if fmt.Sprint(events) != fmt.Sprint(wantEvents) {
		t.Fatalf("events=%v want %v", events, wantEvents)
	}
	eventsMu.Unlock()
	submitted, _ := sink.snapshot()
	if len(submitted) != 3 || submitted[0].Job.ID != "b" || submitted[1].Job.ID != "a" {
		t.Fatalf("submitted=%+v", submitted)
	}
	feeder.Close()
}

func TestFeedOnceRejectsInvalidLimitBeforeSourceAccess(t *testing.T) {
	var calls int
	source := sourceFunc(func(context.Context, int) ([]Reference, error) {
		calls++
		return nil, nil
	})
	resolver := CandidateResolverFunc(func(context.Context, Reference) (ResolvedCandidate, error) {
		return ResolvedCandidate{}, errors.New("must not resolve")
	})
	sink := &fakeSink{}
	feeder, err := New(source, resolver, sink)
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		ctx   context.Context
		limit int
	}{
		{ctx: context.Background(), limit: 0},
		{ctx: context.Background(), limit: -1},
		{ctx: context.Background(), limit: MaxPageSize + 1},
		{ctx: nil, limit: 1},
	} {
		if _, err := feeder.FeedOnce(test.ctx, test.limit); !errors.Is(err, ErrInvalidLimit) {
			t.Fatalf("limit=%d err=%v", test.limit, err)
		}
	}
	if calls != 0 {
		t.Fatalf("source calls=%d", calls)
	}
	feeder.Close()

	redisClient := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1"})
	t.Cleanup(func() { _ = redisClient.Close() })
	rawSource := &RedisDueSource{client: redisClient, configsKey: "configs", readyKey: "ready"}
	if _, err := rawSource.ListDue(context.Background(), MaxPageSize+1); !errors.Is(err, ErrInvalidLimit) {
		t.Fatalf("Redis source invalid limit err=%v", err)
	}
	if connections := redisClient.PoolStats().TotalConns; connections != 0 {
		t.Fatalf("invalid limit opened %d Redis connections", connections)
	}
}

func TestFeedOnceSanitizesSourceFailure(t *testing.T) {
	source := sourceFunc(func(context.Context, int) ([]Reference, error) {
		return nil, errors.New("redis://user:password@private.example")
	})
	resolver := CandidateResolverFunc(func(context.Context, Reference) (ResolvedCandidate, error) {
		return ResolvedCandidate{}, nil
	})
	sink := &fakeSink{}
	feeder, err := New(source, resolver, sink)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := feeder.FeedOnce(context.Background(), 1); !errors.Is(err, ErrSourceRead) ||
		strings.Contains(err.Error(), "password") {
		t.Fatalf("source error=%v", err)
	}
	feeder.Close()
}

func TestFeedOnceRejectsInvalidSourceReferenceBeforeResolution(t *testing.T) {
	tests := []Reference{
		{},
		{TaskID: "   ", ConfigRevision: 1},
		{TaskID: strings.Repeat("x", maxTaskIDBytes+1), ConfigRevision: 1},
		{TaskID: "task", ConfigRevision: 0},
		{TaskID: "task", ConfigRevision: queuev2.RedisCandidateMaxInteger + 1},
	}
	for index, reference := range tests {
		t.Run(fmt.Sprintf("case-%d", index), func(t *testing.T) {
			var resolves int
			source := sourceFunc(func(context.Context, int) ([]Reference, error) {
				return []Reference{reference}, nil
			})
			resolver := CandidateResolverFunc(func(context.Context, Reference) (ResolvedCandidate, error) {
				resolves++
				return ResolvedCandidate{}, nil
			})
			sink := &fakeSink{}
			feeder, err := New(source, resolver, sink)
			if err != nil {
				t.Fatal(err)
			}
			_, err = feeder.FeedOnce(context.Background(), 1)
			if !errors.Is(err, ErrInvalidReference) || resolves != 0 {
				t.Fatalf("err=%v resolves=%d", err, resolves)
			}
			if submitted, _ := sink.snapshot(); len(submitted) != 0 {
				t.Fatalf("submitted=%d", len(submitted))
			}
			feeder.Close()
		})
	}
}

func TestFeedOnceRejectsWholePageForResolverAndProfileFailures(t *testing.T) {
	first := Reference{TaskID: "first", ConfigRevision: 1}
	bad := Reference{TaskID: "bad", ConfigRevision: 2}
	tests := []struct {
		name    string
		resolve func(Reference) (ResolvedCandidate, error)
		want    error
	}{
		{
			name: "resolver error is sanitized",
			resolve: func(reference Reference) (ResolvedCandidate, error) {
				if reference == bad {
					return ResolvedCandidate{}, errors.New("secret resolver detail")
				}
				return candidateFor(reference, "https://first.example/sitemap.xml"), nil
			},
			want: ErrResolve,
		},
		{
			name: "task mismatch",
			resolve: func(reference Reference) (ResolvedCandidate, error) {
				resolved := candidateFor(reference, "https://good.example/sitemap.xml")
				if reference == bad {
					resolved.Candidate.Job.ID = "other"
				}
				return resolved, nil
			},
			want: ErrInvalidReference,
		},
		{
			name: "revision mismatch",
			resolve: func(reference Reference) (ResolvedCandidate, error) {
				resolved := candidateFor(reference, "https://good.example/sitemap.xml")
				if reference == bad {
					resolved.Candidate.ConfigRevision++
				}
				return resolved, nil
			},
			want: ErrInvalidReference,
		},
		{
			name: "browser required",
			resolve: func(reference Reference) (ResolvedCandidate, error) {
				resolved := candidateFor(reference, "https://good.example/sitemap.xml")
				if reference == bad {
					resolved.BrowserRequired = true
				}
				return resolved, nil
			},
			want: ErrUnsupported,
		},
		{
			name: "unsupported cohort",
			resolve: func(reference Reference) (ResolvedCandidate, error) {
				resolved := candidateFor(reference, "https://good.example/sitemap.xml")
				if reference == bad {
					resolved.Cohort = "browser-sitemap"
				}
				return resolved, nil
			},
			want: ErrUnsupported,
		},
		{
			name: "relative URL",
			resolve: func(reference Reference) (ResolvedCandidate, error) {
				rawURL := "https://good.example/sitemap.xml"
				if reference == bad {
					rawURL = "/sitemap.xml"
				}
				return candidateFor(reference, rawURL), nil
			},
			want: ErrUnsupported,
		},
		{
			name: "userinfo URL",
			resolve: func(reference Reference) (ResolvedCandidate, error) {
				rawURL := "https://good.example/sitemap.xml"
				if reference == bad {
					rawURL = "https://user:password@good.example/sitemap.xml"
				}
				return candidateFor(reference, rawURL), nil
			},
			want: ErrUnsupported,
		},
		{
			name: "invalid sitemap bounds",
			resolve: func(reference Reference) (ResolvedCandidate, error) {
				resolved := candidateFor(reference, "https://good.example/sitemap.xml")
				if reference == bad {
					resolved.Candidate.Job.Sitemap.MaxURLs = 0
				}
				return resolved, nil
			},
			want: ErrUnsupported,
		},
		{
			name: "negative job timeout",
			resolve: func(reference Reference) (ResolvedCandidate, error) {
				resolved := candidateFor(reference, "https://good.example/sitemap.xml")
				if reference == bad {
					resolved.Candidate.Job.Timeout = -time.Nanosecond
				}
				return resolved, nil
			},
			want: ErrUnsupported,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			source := sourceFunc(func(context.Context, int) ([]Reference, error) {
				return []Reference{first, bad}, nil
			})
			resolver := CandidateResolverFunc(func(_ context.Context, reference Reference) (ResolvedCandidate, error) {
				return test.resolve(reference)
			})
			sink := &fakeSink{}
			feeder, err := New(source, resolver, sink)
			if err != nil {
				t.Fatal(err)
			}
			report, err := feeder.FeedOnce(context.Background(), 2)
			if !errors.Is(err, test.want) || report.Submitted != 0 {
				t.Fatalf("report=%+v err=%v want=%v", report, err, test.want)
			}
			if strings.Contains(err.Error(), "secret") || strings.Contains(err.Error(), "password") {
				t.Fatalf("error leaked detail: %v", err)
			}
			if submitted, _ := sink.snapshot(); len(submitted) != 0 {
				t.Fatalf("partial page submitted=%d", len(submitted))
			}
			feeder.Close()
		})
	}
}

func TestCloseCancelsBlockedStagesAndClosesSinkOnce(t *testing.T) {
	tests := []struct {
		name  string
		stage string
	}{
		{name: "source", stage: "source"},
		{name: "resolver", stage: "resolver"},
		{name: "submit", stage: "submit"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			entered := make(chan struct{})
			var once sync.Once
			signal := func() { once.Do(func() { close(entered) }) }
			reference := Reference{TaskID: "task", ConfigRevision: 1}
			source := sourceFunc(func(ctx context.Context, _ int) ([]Reference, error) {
				if test.stage == "source" {
					signal()
					<-ctx.Done()
					return nil, ctx.Err()
				}
				return []Reference{reference}, nil
			})
			resolver := CandidateResolverFunc(func(ctx context.Context, reference Reference) (ResolvedCandidate, error) {
				if test.stage == "resolver" {
					signal()
					<-ctx.Done()
					return ResolvedCandidate{}, ctx.Err()
				}
				return candidateFor(reference, "https://good.example/sitemap.xml"), nil
			})
			sink := &fakeSink{}
			if test.stage == "submit" {
				sink.submit = func(ctx context.Context, _ queueworker.Candidate) error {
					signal()
					<-ctx.Done()
					return ctx.Err()
				}
			}
			feeder, err := New(source, resolver, sink)
			if err != nil {
				t.Fatal(err)
			}
			finished := make(chan error, 1)
			go func() {
				_, feedErr := feeder.FeedOnce(context.Background(), 1)
				finished <- feedErr
			}()
			select {
			case <-entered:
			case <-time.After(time.Second):
				t.Fatal("stage did not block")
			}
			feeder.Close()
			feeder.Close()
			select {
			case feedErr := <-finished:
				if !errors.Is(feedErr, ErrClosed) {
					t.Fatalf("feed error=%v", feedErr)
				}
			case <-time.After(time.Second):
				t.Fatal("blocked FeedOnce leaked")
			}
			submitted, closes := sink.snapshot()
			if len(submitted) != 0 || closes != 1 {
				t.Fatalf("submitted=%d closes=%d", len(submitted), closes)
			}
			if _, err := feeder.FeedOnce(context.Background(), 1); !errors.Is(err, ErrClosed) {
				t.Fatalf("post-close error=%v", err)
			}
		})
	}
}

func TestParentCancellationDuringResolutionPreventsSubmission(t *testing.T) {
	reference := Reference{TaskID: "task", ConfigRevision: 1}
	entered := make(chan struct{})
	source := sourceFunc(func(context.Context, int) ([]Reference, error) {
		return []Reference{reference}, nil
	})
	resolver := CandidateResolverFunc(func(ctx context.Context, _ Reference) (ResolvedCandidate, error) {
		close(entered)
		<-ctx.Done()
		return ResolvedCandidate{}, ctx.Err()
	})
	sink := &fakeSink{}
	feeder, err := New(source, resolver, sink)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	finished := make(chan error, 1)
	go func() {
		_, feedErr := feeder.FeedOnce(ctx, 1)
		finished <- feedErr
	}()
	<-entered
	cancel()
	if err := <-finished; !errors.Is(err, context.Canceled) {
		t.Fatalf("feed error=%v", err)
	}
	if submitted, _ := sink.snapshot(); len(submitted) != 0 {
		t.Fatalf("submitted=%d", len(submitted))
	}
	feeder.Close()
}
