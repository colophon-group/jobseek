package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/cookiejar"

	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	join "github.com/colophon-group/jobseek/apps/crawler/go/join-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	oracle "github.com/colophon-group/jobseek/apps/crawler/go/oracle-hcm"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	publisherpolicy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	smartrecruiters "github.com/colophon-group/jobseek/apps/crawler/go/smartrecruiters-monitor"
	workable "github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor"
)

// Existing API parsers retain their own status, one-shot and fallback behavior.
// The worker supplies the sealed transport and the original request observation;
// no child process, additional publisher probe or persistence path is introduced.
func fetchAPIDetail(ctx context.Context, verified *VerifiedDirectHTTP, profile queue.WorkdayDetailProfile) (map[string]any, *publisherpolicy.Reservation, error) {
	if verified == nil || verified.client == nil || verified.proxyRequired != queue.ProfileRequiresProxy(profile.Profile) {
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
	case "seek.graphql-detail/v1":
		content, reservation, err = fetchSeekDetail(ctx, &client, profile, pauseRich)
	case "pdf.public-detail/v1":
		content, reservation, err = fetchPDFDetail(ctx, &client, profile)
	case "notion.public-detail/v1":
		content, reservation, err = fetchNotionDetail(ctx, &client, profile)
	case "adp.public-detail/v1":
		content, reservation, err = fetchADPDetail(ctx, &client, profile)
	case "paylocity.html-detail/v1", "paylocity.proxy-html-detail/v1":
		client.CheckRedirect = verified.client.CheckRedirect
		content, reservation, err = fetchPaylocityDetail(ctx, &client, profile.SourceURL)
	case "paycom.public-detail/v1":
		content, reservation, err = fetchPaycomDetail(ctx, &client, profile)
	case "rippling.v1-detail/v1":
		content, reservation, err = fetchRipplingDetail(ctx, &client, profile.SourceURL, profile.APITokenOverride)

	case "mokahr.encrypted-detail/v1":
		client.CheckRedirect = verified.client.CheckRedirect
		content, reservation, err = fetchMokahrDetail(ctx, &client, profile.SourceURL, profile.APILocale)
	case "eightfold.jsonld-api-detail/v1", "eightfold.proxy-jsonld-api-detail/v1":
		client.CheckRedirect = verified.client.CheckRedirect
		content, reservation, err = fetchEightfoldDetail(ctx, &client, profile.SourceURL, profile.JSONLDConfig)
	case "api_sniffer.http-detail/v1", "api_sniffer.proxy-http-detail/v1":
		// This configured route follows the Python shared client redirect contract.
		client.CheckRedirect = verified.client.CheckRedirect
		content, reservation, err = fetchHTTPAPIDetail(ctx, &client, profile)
	case "oracle_hcm.api-detail/v1":
		body, response, failure := fetchOraclePage(ctx, &client, profile.Endpoint)
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		if response != nil && response.reserved {
			reservation = &publisherpolicy.Reservation{URL: response.finalURL, Source: "header", PolicyURL: response.policy}
		} else if failure != nil {
			err = executor.ErrEmptyResult
		} else {
			content, err = projectOracleDetail(body, profile.OracleFields)
			if err != nil {
				err = executor.ErrEmptyResult
			}
		}
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

// Configured Oracle fields use the already verified Python-compatible API field
// extractor. Tenant/site selection remains bound by the Oracle detail profile.
func projectOracleDetail(body []byte, fields map[string]any) (map[string]any, error) {
	if len(fields) == 0 {
		return oracle.ProjectDetail(body)
	}
	doc, err := apisniffer.Decode(body)
	if err != nil {
		return nil, err
	}
	row, err := apisniffer.Search(doc.Value, "items[0]")
	if err != nil {
		return nil, err
	}
	if _, ok := row.(map[string]any); !ok {
		return nil, executor.ErrEmptyResult
	}
	values := map[string]any{}
	for target, spec := range fields {
		value, err := doc.Field(row, spec)
		if err != nil {
			return nil, err
		}
		values[target] = value
	}
	return values, nil
}
