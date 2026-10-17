package main

import (
	"context"
	"encoding/json"
	"math/rand/v2"
	"net/url"
	"strconv"
	"time"

	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

// Only the root is navigated/rendered. The original pagination.browser path
// fetches later raw HTML in that target, retaining its cookies and connection.
func executeDOMTraversal(ctx, target context.Context, task Task) (Result, error) {
	request := task.Feed.request
	root := true
	return task.Feed.converse(ctx, func(call context.Context, endpoint string) (Result, error) {
		if root {
			root = false
			if endpoint != task.URL {
				return Result{}, feed.ErrProtocol
			}
			step := task
			step.Feed = nil
			step.ResponseBodyLimit = 0
			result, err := executeOnTarget(call, target, step)
			if err != nil {
				return Result{}, err
			}
			result.ResponseBody = []byte(result.HTML)
			if len(result.ResponseBody) > feed.BodyLimit {
				return Result{}, errResourceLimit
			}
			return result, nil
		}
		for attempt := 0; attempt < request.DOM.Attempts; attempt++ {
			result, err := fetchDOMTraversalPage(call, target, request.FeedURL, endpoint)
			if call.Err() != nil {
				return Result{}, call.Err()
			}
			if err == nil {
				classification, classifyErr := dom.ClassifyDocument(string(result.ResponseBody), dom.Object{}, endpoint)
				if classifyErr != nil || classification["classification"] == "challenge" || policy.Check(result.ResourcePolicy, string(result.ResponseBody), result.FinalURL) != nil {
					return result, nil
				}
				retry := result.Status >= 500 || result.Status == 408 || result.Status == 425 || result.Status == 429 || request.DOM.Transient403 && (result.Status == 401 || result.Status == 403) || result.Status == 200 && len(result.ResponseBody) == 0
				if !retry || attempt+1 == request.DOM.Attempts {
					return result, nil
				}
			} else if attempt+1 == request.DOM.Attempts {
				return Result{}, err
			}
			timer := time.NewTimer(time.Duration(float64(time.Duration(1<<attempt)*500*time.Millisecond) * (0.5 + rand.Float64())))
			select {
			case <-call.Done():
				timer.Stop()
				return Result{}, call.Err()
			case <-timer.C:
			}
		}
		return Result{}, feed.ErrProtocol
	})
}

func fetchDOMTraversalPage(ctx, target context.Context, root, endpoint string) (Result, error) {
	encoded, err := json.Marshal(endpoint)
	if err != nil {
		return Result{}, feed.ErrProtocol
	}
	expression := `(async(u)=>{const r=await fetch(u);const body=await r.text();if(body.length>` + strconv.Itoa(feed.BodyLimit) + `)throw new Error("document limit");return {status:r.status,url:r.url,body,reservation:r.headers.get("tdm-reservation"),policy:r.headers.get("tdm-policy")};})(` + string(encoded) + `)`
	var response replayFetchResponse
	callTarget, cancel := context.WithCancel(target)
	defer cancel()
	stop := context.AfterFunc(ctx, cancel)
	defer stop()
	err = chromedp.Run(callTarget, chromedp.ActionFunc(func(call context.Context) error {
		object, exception, err := runtime.Evaluate(expression).WithReturnByValue(true).WithAwaitPromise(true).Do(call)
		if err != nil || exception != nil || object == nil || len(object.Value) > 4*feed.BodyLimit || json.Unmarshal(object.Value, &response) != nil {
			return feed.ErrProtocol
		}
		return nil
	}))
	if ctx.Err() != nil {
		return Result{}, ctx.Err()
	}
	if err != nil {
		return Result{}, feed.ErrProtocol
	}
	requested, e1 := url.Parse(root)
	final, e2 := url.Parse(response.URL)
	if e1 != nil || e2 != nil || final.Scheme != requested.Scheme || final.Host != requested.Host || final.User != nil || len(response.Body) > feed.BodyLimit || response.Status < 100 || response.Status > 599 {
		return Result{}, feed.ErrProtocol
	}
	return Result{Status: response.Status, FinalURL: response.URL, ResponseBody: []byte(response.Body), ResourcePolicy: &runtimev1.ResourcePolicySignals{TdmReservationHeader: response.Reservation, TdmPolicyHeader: response.Policy}}, nil
}
