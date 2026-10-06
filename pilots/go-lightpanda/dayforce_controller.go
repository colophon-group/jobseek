package main

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"time"

	df "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/dayforcesession"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	resourcepolicy "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/resourcepolicy"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policycheck "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type dayforceFetch func(context.Context, int) (df.Page, error)
type dayforceConversation func(context.Context, df.Ready, dayforceFetch) error
type dayforceTask struct {
	request  df.Request
	converse dayforceConversation
}

func durationDayforce(ms uint64) time.Duration { return time.Duration(ms) * time.Millisecond }

// Called inside the fresh target, before its deferred disposal. Only public
// bootstrap/site data and compiled search results reach the caller. Credentials
// remain controller-local and all search commands stay on this one target.
func executeDayforceConversation(ctx context.Context, task *dayforceTask, capture *dayforceSearchCapture, status int, finalURL, html string, policy *runtimev1.ResourcePolicySignals) error {
	if task == nil || capture == nil || !resourcepolicy.Valid(policy) {
		return errDayforceSession
	}
	r := task.request
	board := api.DayforceBoard{Tenant: r.Tenant, Portal: r.Portal}
	if !board.ResourceMatches(finalURL) || finalURL == board.SearchURL() {
		return errDayforceSession
	}
	ready := df.Ready{Status: status, FinalURL: finalURL, Policy: policy}
	if err := policycheck.Check(policy, html, finalURL); err != nil {
		var reservation *policycheck.Reservation
		if !errors.As(err, &reservation) {
			return errDayforceSession
		}
		ready.Reservation = &df.Reservation{Source: reservation.Source, PolicyURL: reservation.PolicyURL}
	}
	ready.PublisherChecked = true
	site, e := api.DayforceExtractSite(html, board)
	if e == nil {
		ready.Site = df.Site{JobBoardID: site.JobBoardID, Culture: site.Culture, Cultures: site.Cultures, Disabled: site.Disabled}
	}
	return task.converse(ctx, ready, func(fetchCtx context.Context, offset int) (df.Page, error) {
		if e != nil || status != 200 || !reflect.DeepEqual(ready.Site, r.ExpectedSite) {
			return df.Page{}, errDayforceSession
		}
		headers, _, e := capture.wait(fetchCtx)
		if e != nil {
			return df.Page{}, e
		}
		body, e := api.DayforceSearchBody(board, site, offset)
		if e != nil {
			return df.Page{}, errDayforceSession
		}
		response, e := fetchDayforceInSession(fetchCtx, board.SearchURL(), headers, body)
		if fetchCtx.Err() != nil {
			return df.Page{}, fetchCtx.Err()
		}
		if e != nil {
			return df.Page{Offset: offset, FinalURL: board.SearchURL(), TransportFailed: true}, nil
		}
		// Even a reflecting origin cannot export the ephemeral token in a body
		// or a publisher header. Do not retain or log the offending response.
		if strings.Contains(response.Body, headers.token) || response.Reservation != nil && strings.Contains(*response.Reservation, headers.token) || response.Policy != nil && strings.Contains(*response.Policy, headers.token) {
			return df.Page{}, errDayforceSession
		}
		signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: response.Reservation, TdmPolicyHeader: response.Policy}
		if !resourcepolicy.Valid(signals) {
			return df.Page{}, errDayforceSession
		}
		return df.Page{Offset: offset, FinalURL: response.URL, Status: response.Status, Body: []byte(response.Body), Policy: signals}, nil
	})
}
