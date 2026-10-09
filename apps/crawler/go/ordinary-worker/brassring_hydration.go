package worker

import (
	"context"
	"errors"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"golang.org/x/sync/errgroup"
)

type brassRingDetailScope struct{ board string }

func (s brassRingDetailScope) ResourceMatches(resource string) bool {
	return api.BrassRingDetailResourceMatches(s.board, resource)
}
func (brassRingDetailScope) RequestTimeout() time.Duration { return 60 * time.Second }

// The validated browser snapshot is provisional until every missing location
// has a matching public detail preload. No successful prefix grants absence.
func hydrateBrassRingSnapshot(ctx context.Context, client *http.Client, board string, snapshot RichDiscovery) (RichDiscovery, error) {
	empty := RichDiscovery{Jobs: []RichMonitorJob{}}
	if ctx.Err() != nil {
		return empty, ctx.Err()
	}
	scope := brassRingDetailScope{board}
	missing := []int{}
	for i, job := range snapshot.Jobs {
		id, ok := job.Metadata["requisition_id"].(string)
		u, err := url.Parse(job.URL)
		if !ok || id == "" || err != nil || u.Query().Get("jobid") != id || !scope.ResourceMatches(job.URL) {
			return empty, queue.ErrConfiguration
		}
		if len(job.Locations) == 0 {
			missing = append(missing, i)
		}
	}
	if len(missing) == 0 {
		return snapshot, nil
	}
	if client == nil {
		return empty, queue.ErrConfiguration
	}
	op := *client
	var err error
	op.Jar, err = cookiejar.New(nil)
	if err != nil {
		return empty, err
	}
	op.CheckRedirect = func(request *http.Request, via []*http.Request) error {
		if len(via) > 5 || !scope.ResourceMatches(request.URL.String()) {
			return http.ErrUseLastResponse
		}
		return nil
	}
	// Keep provisional mutations private even when the caller retains its input.
	snapshot.Jobs = append([]RichMonitorJob(nil), snapshot.Jobs...)
	group, call := errgroup.WithContext(ctx)
	group.SetLimit(8)
	observations := make([]*GreenhouseResponse, len(snapshot.Jobs))
	for _, i := range missing {
		group.Go(func() error {
			job := &snapshot.Jobs[i]
			body, observed, err := fetchProviderResource(call, &op, scope, job.URL, nil, nil, 16<<20)
			observations[i] = observed
			if err != nil {
				return err
			}
			job.Locations, err = api.BrassRingDetailLocation(string(body), job.Metadata["requisition_id"].(string))
			return err
		})
	}
	if err := group.Wait(); err != nil {
		var reserved *policy.Reservation
		if errors.As(err, &reserved) {
			for _, observed := range observations {
				if observed != nil && observed.reserved && observed.finalURL == reserved.URL {
					empty.Response = observed
					break
				}
			}
		}
		return empty, err
	}
	if ctx.Err() != nil {
		return empty, ctx.Err()
	}
	return snapshot, nil
}
