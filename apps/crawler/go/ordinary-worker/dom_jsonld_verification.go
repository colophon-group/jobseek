package worker

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"net/http"
	"sort"
	"sync"
	"time"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type domVerificationBinding struct {
	parent, boardID, effective, resource string
	config                               [32]byte
}

func domVerificationConfig(config map[string]string) [32]byte {
	body, _ := json.Marshal(config)
	return sha256.Sum256(body)
}
func domVerificationResponseMatches(p queue.GreenhouseMonitorProfile, config map[string]string, r *GreenhouseResponse) bool {
	if r == nil || r.domVerification == nil || p.Provider != "dom" {
		return false
	}
	b := r.domVerification
	c, e := queue.DOMMonitorOptions(config)
	return e == nil && (c.RequireJSONLD || c.HasDetailFilters()) && p.Endpoint == config["board_url"] && b.parent == p.Endpoint && b.boardID == p.BoardID && b.effective == p.EffectiveConfigSHA256 && b.resource == r.endpoint && b.config == domVerificationConfig(config)
}

// The private binding comes from this complete listing's exact candidate set,
// not a broad host or URL-pattern allowance for publisher policy observations.
func fetchDOMVerification(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, source string, wait func(context.Context, time.Duration) error) (bool, *GreenhouseResponse, error) {
	return fetchDOMVerificationClassified(ctx, client, p, config, source, wait, func(body string) (bool, error) { return jsonld.ContainsJobPosting([]byte(body), ""), nil }, false)
}
func fetchDOMVerificationClassified(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, source string, wait func(context.Context, time.Duration) error, classify func(string) (bool, error), strict bool) (bool, *GreenhouseResponse, error) {
	var observed *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		if e := ctx.Err(); e != nil {
			return false, observed, e
		}
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		doc, e := jsonld.FetchDocumentWithClient(requestCtx, source, jsonld.DocumentOptions{}, client)
		cancel()
		if doc.Responses > 0 {
			var policy *string
			if doc.TDMPolicy != "" {
				value := doc.TDMPolicy
				policy = &value
			}
			observed = &GreenhouseResponse{endpoint: source, finalURL: doc.FinalURL, status: doc.Status, bytes: len(doc.Body), reserved: doc.ErrorKind == "tdm", policy: policy, reservationSource: doc.TDMSource,
				domVerification: &domVerificationBinding{p.Endpoint, p.BoardID, p.EffectiveConfigSHA256, source, domVerificationConfig(config)}}
		}
		if doc.ErrorKind == "tdm" {
			return false, observed, e
		}
		if ctx.Err() != nil {
			return false, observed, ctx.Err()
		}
		if e == nil && doc.Status == 200 && len(doc.Body) > 0 {
			body := jsonld.DecodeDocument(doc.Body, doc.ContentType)
			classification, err := dom.ClassifyDocument(body, dom.Object{}, source)
			if err != nil || classification["classification"] == "challenge" {
				return false, observed, &DiscoveryError{Kind: "inventory_failed"}
			}
			keep, err := classify(body)
			return keep, observed, err
		}
		retry := e != nil || doc.Status == 200 || doc.Status == 401 || doc.Status == 403 || doc.Status == 408 || doc.Status == 425 || doc.Status == 429 || doc.Status >= 500
		if !retry {
			if strict && doc.Status != 404 && doc.Status != 410 {
				return false, observed, &DiscoveryError{Kind: "inventory_failed", Status: doc.Status}
			}
			return false, observed, nil
		}
		if attempt == 2 {
			return false, observed, &DiscoveryError{Kind: "inventory_failed", Status: doc.Status, cause: e}
		}
		if err := wait(ctx, time.Duration(500*(1<<attempt))*time.Millisecond); err != nil {
			return false, observed, err
		}
	}
	return false, observed, &DiscoveryError{Kind: "inventory_failed"}
}
func verifyDOMJobPostingInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, in RichDiscovery) (RichDiscovery, error) {
	return verifyDOMJobPostingInventoryWithWait(ctx, client, p, config, in, pauseRich)
}
func verifyDOMJobPostingInventoryWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, in RichDiscovery, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	return verifyDOMInventoryClassified(ctx, client, p, config, in, wait, nil, false)
}
func verifyDOMInventoryClassified(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, in RichDiscovery, wait func(context.Context, time.Duration) error, classify func(string) (bool, error), strict bool) (RichDiscovery, error) {
	if client == nil || len(in.Jobs) > 500 {
		in.Jobs = nil
		return in, &DiscoveryError{Kind: "inventory_failed"}
	}
	jobs := append([]RichMonitorJob{}, in.Jobs...)
	sort.Slice(jobs, func(i, j int) bool { return jobs[i].URL < jobs[j].URL })
	scoped := *client
	scoped.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	workCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	kept := make([]bool, len(jobs))
	failures := make([]error, len(jobs))
	observed := make([]*GreenhouseResponse, len(jobs))
	tasks := make(chan int)
	var wg sync.WaitGroup
	for worker := 0; worker < min(8, len(jobs)); worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range tasks {
				if workCtx.Err() != nil {
					continue
				}
				if classify == nil {
					kept[i], observed[i], failures[i] = fetchDOMVerification(workCtx, &scoped, p, config, jobs[i].URL, wait)
				} else {
					kept[i], observed[i], failures[i] = fetchDOMVerificationClassified(workCtx, &scoped, p, config, jobs[i].URL, wait, classify, strict)
				}
				if failures[i] != nil {
					cancel()
				}
			}
		}()
	}
	for i := range jobs {
		if workCtx.Err() != nil {
			break
		}
		select {
		case tasks <- i:
		case <-workCtx.Done():
		}
	}
	close(tasks)
	wg.Wait()
	for _, r := range observed {
		if r != nil && r.reserved {
			in.Jobs = nil
			in.Response = r
			return in, &DiscoveryError{Kind: "publisher_reserved", Status: r.status}
		}
	}
	for _, e := range failures {
		if e != nil && !errors.Is(e, context.Canceled) {
			in.Jobs = nil
			return in, e
		}
	}
	if e := ctx.Err(); e != nil {
		in.Jobs = nil
		return in, e
	}
	for _, e := range failures {
		if e != nil {
			in.Jobs = nil
			return in, e
		}
	}
	in.Jobs = []RichMonitorJob{}
	for i, j := range jobs {
		if kept[i] {
			in.Jobs = append(in.Jobs, j)
		}
	}
	return in, nil
}
