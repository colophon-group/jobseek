package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/cookiejar"

	join "github.com/colophon-group/jobseek/apps/crawler/go/join-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	publisherpolicy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	smartrecruiters "github.com/colophon-group/jobseek/apps/crawler/go/smartrecruiters-monitor"
	workable "github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor"
)

// Existing API parsers retain their own status, one-shot and fallback behavior.
// The worker supplies the sealed transport and the original request observation;
// no child process, additional publisher probe or persistence path is introduced.
func fetchAPIDetail(ctx context.Context, verified *VerifiedDirectHTTP, profile queue.WorkdayDetailProfile) (map[string]any, *publisherpolicy.Reservation, error) {
	if verified == nil || verified.client == nil {
		return nil, nil, queue.ErrConfiguration
	}
	client := *verified.client
	jar, err := cookiejar.New(nil)
	if err != nil {
		return nil, nil, err
	}
	client.Jar = jar
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	var content any
	var reservation *publisherpolicy.Reservation
	switch profile.Profile {
	case "join.nextdata-detail/v1":
		fetched, failure := join.FetchDetailWithClient(ctx, join.DetailRequest{URL: profile.SourceURL, Config: profile.JoinDetailConfig}, &client)
		content, err = fetched.Content, failure
		if fetched.ErrorKind == "tdm" {
			reservation = &publisherpolicy.Reservation{URL: fetched.ReservationURL, Source: fetched.TDMSource}
			if fetched.TDMPolicy != "" {
				reservation.PolicyURL = &fetched.TDMPolicy
			}
		}
	case "smartrecruiters.api-detail/v1":
		fetched, failure := smartrecruiters.FetchDetailWithClient(ctx, profile.SourceURL, &client)
		content, err = fetched.Content, failure
		var reserved *smartrecruiters.Failure
		if errors.As(err, &reserved) && reserved.Kind == "tdm" {
			reservation = &publisherpolicy.Reservation{URL: reserved.URL, Source: reserved.Source}
			if reserved.Policy != "" {
				reservation.PolicyURL = &reserved.Policy
			}
		}
	case "workable.api-detail/v1":
		fetched, failure := workable.FetchDetailWithClient(ctx, profile.SourceURL, profile.APITokenOverride, &client)
		content, err = fetched.Content, failure
		if fetched.ErrorKind == "tdm" {
			reservation = &publisherpolicy.Reservation{URL: fetched.FinalURL, Source: fetched.TDMSource}
			if fetched.TDMPolicy != "" {
				reservation.PolicyURL = &fetched.TDMPolicy
			}
		}
	default:
		return nil, nil, queue.ErrUnsupportedProfile
	}
	if err != nil || reservation != nil {
		return nil, reservation, err
	}
	// Preserve the existing JobContent JSON boundary, including typed arrays,
	// optional scalars and metadata, before shared enrichment and canonical writes.
	body, err := json.Marshal(content)
	if err != nil {
		return nil, nil, err
	}
	var values map[string]any
	if err := json.Unmarshal(body, &values); err != nil {
		return nil, nil, err
	}
	return values, nil, nil
}
