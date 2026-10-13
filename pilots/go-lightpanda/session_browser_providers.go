package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func sessionEvaluate(ctx context.Context, expression string, out any) error {
	target := chromedp.FromContext(ctx)
	if target == nil || target.Target == nil {
		return errReplayCapture
	}
	object, exception, err := runtime.Evaluate(expression).WithReturnByValue(true).WithAwaitPromise(true).Do(cdp.WithExecutor(ctx, target.Target))
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if err != nil || exception != nil || object == nil || len(object.Value) > 4*(16<<20) || json.Unmarshal(object.Value, out) != nil {
		return errReplayCapture
	}
	return nil
}

func sessionWait(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func sessionPoll(ctx context.Context, expression string, timeout time.Duration) error {
	call, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	for {
		var ready bool
		if err := sessionEvaluate(call, expression, &ready); err != nil {
			return err
		}
		if ready {
			return nil
		}
		if err := sessionWait(call, 50*time.Millisecond); err != nil {
			return err
		}
	}
}

const brassRingPageExpression = `(()=>{const p=document.querySelector('.pagewise-pagination[aria-current="page"]');return p&&p.textContent.trim()===`

type sessionControlError struct {
	phase string
	cause error
}

func (e *sessionControlError) Error() string { return "native session control failed: " + e.phase }
func (e *sessionControlError) Unwrap() error { return e.cause }
func sessionFailure(phase string, err error) error {
	if err == nil {
		return nil
	}
	return &sessionControlError{phase, err}
}

// Every click has one response expectation and one committed-page barrier.
// Ambiguous clicks never retry on the same session and skip a page.
func brassRingClick(ctx context.Context, capture *replayCapture, selector, responsePath string, expectedPage int) (document *api.Document, failure error) {
	phase := "response"
	defer func() { failure = sessionFailure(fmt.Sprintf("brassring page %d %s", expectedPage, phase), failure) }()
	if err := capture.beginLivePass(); err != nil {
		return nil, err
	}
	quoted, _ := json.Marshal(selector)
	phase = "control-ready"
	if err := sessionPoll(ctx, `(()=>{const e=document.querySelector(`+string(quoted)+`);if(!e||e.disabled||e.hidden)return false;const s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"})()`, 60*time.Second); err != nil {
		return nil, err
	}
	phase = "click"
	var clicked bool
	if err := sessionEvaluate(ctx, `(()=>{const e=document.querySelector(`+string(quoted)+`);if(!e)return false;e.click();return true})()`, &clicked); err != nil || !clicked {
		return nil, errReplayCapture
	}
	call, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	for {
		phase = "response"
		document, ready, err := capture.sessionDocument(call, responsePath, readReplayResponseBody)
		if err != nil {
			return nil, err
		}
		if ready {
			if expectedPage > 0 {
				phase = "page-barrier"
				if err := sessionPoll(call, brassRingPageExpression+strconv.Quote(strconv.Itoa(expectedPage))+`})()`, 60*time.Second); err != nil {
					return nil, err
				}
			}
			return document, nil
		}
		if err := sessionWait(call, 50*time.Millisecond); err != nil {
			return nil, err
		}
	}
}

// Consume one expected exchange inside the live controller. Headers, cookies
// and response bodies never enter the public frame or diagnostic formatting.
func (capture *replayCapture) sessionDocument(ctx context.Context, path string, read func(context.Context, network.RequestID) ([]byte, error)) (*api.Document, bool, error) {
	capture.mu.Lock()
	failure, closed := capture.failure, capture.closed
	capture.mu.Unlock()
	if failure != nil {
		return nil, false, failure
	}
	if closed {
		return nil, false, errReplayCapture
	}
	select {
	case id := <-capture.completed:
		capture.mu.Lock()
		pending, ok := capture.pending[id]
		delete(capture.pending, id)
		capture.mu.Unlock()
		u, e := url.Parse(pending.source)
		if !ok || e != nil || !strings.EqualFold(u.Path, path) || pending.status != 200 {
			return nil, false, errReplayCapture
		}
		body, e := read(ctx, id)
		if e != nil {
			return nil, false, errReplayCapture
		}
		if len(body) > capture.bodyLimit {
			return nil, false, errResourceLimit
		}
		if replayReflectsCredentials(pending.headers, string(body), pending.signals.TdmReservationHeader, pending.signals.TdmPolicyHeader) {
			return nil, false, errReplayCredentialResponse
		}
		if e := policy.Check(pending.signals, string(body), pending.source); e != nil {
			return nil, false, e
		}
		d, e := api.Decode(body)
		if e != nil {
			return nil, false, errReplayCapture
		}
		return d, true, nil
	default:
		return nil, false, nil
	}
}

func discoverBrassRingSession(ctx context.Context, task *apiReplayTask, capture *replayCapture) (api.Inventory, error) {
	board, err := api.BrassRingBoardFromURL(task.boardURL)
	if err != nil {
		return api.Inventory{}, err
	}
	controls := api.BrassRingControls{
		Search: func(ctx context.Context) (*api.Document, error) {
			return brassRingClick(ctx, capture, "#clearResumeJobsBtn", "/TGnewUI/Search/Ajax/MatchedJobs", 0)
		},
		Sort: func(ctx context.Context) (*api.Document, error) {
			if err := sessionPoll(ctx, brassRingPageExpression+`"1"})()`, 60*time.Second); err != nil {
				return nil, err
			}
			var index int
			if err := sessionEvaluate(ctx, `(()=>{const a=Array.from(document.querySelectorAll('#sortBy option'));return a.findIndex(o=>o.value==='1')+1})()`, &index); err != nil || index < 1 {
				return nil, api.ErrBrassRingSnapshotChanged
			}
			var clicked bool
			if err := sessionEvaluate(ctx, `(()=>{const e=document.querySelector('#sortBy-button');if(!e)return false;e.click();return true})()`, &clicked); err != nil || !clicked {
				return nil, api.ErrBrassRingSnapshotChanged
			}
			return brassRingClick(ctx, capture, "#sortBy-menu li:nth-child("+strconv.Itoa(index)+")", "/TGnewUI/Search/Ajax/ProcessSortAndShowMoreJobs", 1)
		},
		Next: func(ctx context.Context, p int) (*api.Document, error) {
			return brassRingClick(ctx, capture, `button[title="Next Page"]:not(.disabled-link)`, "/TGnewUI/Search/Ajax/ProcessSortAndShowMoreJobs", p)
		},
	}
	return api.DiscoverBrassRing(ctx, board, controls, func(s string) (*string, error) { return &s, nil }, func(ctx context.Context, source, id string) ([]string, error) {
		if !api.BrassRingDetailMatches(task.boardURL, source) {
			return nil, errReplayCapture
		}
		o := api.NativeBrowserRequestOptions(task.options, api.Request{Method: "GET", URL: source})
		expression, err := replayFetchExpression(o, api.Request{Method: "GET", URL: source})
		if err != nil {
			return nil, err
		}
		var response replayFetchResponse
		if err := sessionEvaluate(ctx, expression, &response); err != nil {
			return nil, err
		}
		if !api.BrassRingDetailMatches(task.boardURL, response.URL) || len(response.Body) > 16<<20 {
			return nil, errReplayCapture
		}
		if err := policy.Check(&runtimev1.ResourcePolicySignals{TdmReservationHeader: response.Reservation, TdmPolicyHeader: response.Policy}, response.Body, response.URL); err != nil {
			return nil, err
		}
		if response.Status < 200 || response.Status >= 300 {
			return nil, errReplayCapture
		}
		return api.BrassRingDetailLocation(response.Body, id)
	})
}

type njoynDocumentState struct {
	mu      sync.Mutex
	current mainDocumentResponse
	frame   cdp.FrameID
}

func (state *njoynDocumentState) observe(event any) {
	e, ok := event.(*network.EventResponseReceived)
	if !ok || e.Response == nil || e.Type != network.ResourceTypeDocument || e.FrameID != state.frame {
		return
	}
	signals, invalid := mainDocumentPolicySignals(e.Response.Headers)
	state.mu.Lock()
	state.current = mainDocumentResponse{resourcePolicy: signals, policyInvalid: invalid, status: e.Response.Status, url: e.Response.URL, loaderID: e.LoaderID, requestID: e.RequestID}
	state.mu.Unlock()
}

const njoynSnapshotExpression = `(()=>{const f=Array.from(document.forms).find(f=>f.elements.namedItem('pn'));const p=f&&f.elements.namedItem('pn');const links=Array.from(document.querySelectorAll('a[href]'),a=>a.href);const text=document.body?document.body.innerText:'';if(links.length>50000||text.length>16000000)throw new Error('Njoyn state bound');return {links,text,pageNumber:p&&'value' in p?p.value:''}})()`

func njoynSnapshot(ctx context.Context, board api.NjoynBoard, state *njoynDocumentState) (api.NjoynSnapshot, error) {
	var html, source string
	if err := chromedp.Run(ctx, chromedp.OuterHTML("html", &html, chromedp.ByQuery), chromedp.Location(&source)); err != nil {
		return api.NjoynSnapshot{}, errReplayCapture
	}
	target := chromedp.FromContext(ctx)
	tree, err := page.GetFrameTree().Do(cdp.WithExecutor(ctx, target.Target))
	if err != nil || len(html) > 16<<20 {
		return api.NjoynSnapshot{}, errReplayCapture
	}
	state.mu.Lock()
	current := state.current
	state.mu.Unlock()
	if current.policyInvalid {
		return api.NjoynSnapshot{}, policy.ErrSignals
	}
	if _, err := correlateMainDocumentSnapshot(current, mainDocumentFrame{url: tree.Frame.URL, loaderID: tree.Frame.LoaderID}, source); err != nil {
		return api.NjoynSnapshot{}, err
	}
	if err := policy.Check(current.resourcePolicy, html, source); err != nil {
		return api.NjoynSnapshot{}, err
	}
	actual, e := api.NjoynBoardFromURL(source)
	if e != nil || actual.Host != board.Host || actual.Tenant != board.Tenant || current.status < 200 || current.status >= 300 {
		return api.NjoynSnapshot{}, errReplayCapture
	}
	classification, e := dom.ClassifyRendered(html, dom.Object{}, source)
	if e != nil || classification["classification"] != "okay" {
		return api.NjoynSnapshot{}, errReplayCapture
	}
	var snapshot struct {
		Links      []string `json:"links"`
		Text       string   `json:"text"`
		PageNumber string   `json:"pageNumber"`
	}
	if err := sessionEvaluate(ctx, njoynSnapshotExpression, &snapshot); err != nil {
		return api.NjoynSnapshot{}, err
	}
	return api.NjoynSnapshotFromState(board, snapshot.Text, snapshot.PageNumber, snapshot.Links)
}

func discoverNjoynSession(ctx context.Context, task *apiReplayTask) (api.Inventory, error) {
	board, err := api.NjoynBoardFromURL(task.boardURL)
	if err != nil {
		return api.Inventory{}, err
	}
	maxPages, attempts, waitMS, navigationMS, err := api.NjoynSessionLimits(task.nativeMetadata)
	if err != nil {
		return api.Inventory{}, err
	}
	target := chromedp.FromContext(ctx)
	if target == nil || target.Target == nil {
		return api.Inventory{}, errReplayCapture
	}
	tree, err := page.GetFrameTree().Do(cdp.WithExecutor(ctx, target.Target))
	if err != nil {
		return api.Inventory{}, errReplayCapture
	}
	state := &njoynDocumentState{frame: tree.Frame.ID}
	listen, cancel := context.WithCancel(ctx)
	defer cancel()
	chromedp.ListenTarget(listen, state.observe)
	controls := api.NjoynControls{Wait: sessionWait}
	controls.First = func(ctx context.Context) (api.NjoynSnapshot, error) {
		result, err := executeOnTarget(ctx, ctx, Task{URL: task.boardURL, Navigation: &navigationOptions{wait: runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED, timeout: time.Duration(task.options.TimeoutMS) * time.Millisecond}})
		if err != nil {
			return api.NjoynSnapshot{}, err
		}
		if err := policy.Check(result.ResourcePolicy, result.HTML, result.FinalURL); err != nil {
			return api.NjoynSnapshot{}, err
		}
		return njoynSnapshot(ctx, board, state)
	}
	controls.Page = func(ctx context.Context, index int) (api.NjoynSnapshot, error) {
		state.mu.Lock()
		previousLoader := state.current.loaderID
		state.mu.Unlock()
		var submitted bool
		expression := `(()=>{const f=Array.from(document.forms).find(f=>f.elements.namedItem('pn'));const p=f&&f.elements.namedItem('pn');if(!f||!p||!('value' in p))return false;const a=new URL(f.action,location.href);if(a.origin!==location.origin||!a.pathname.toLowerCase().includes('/xweb/'))return false;p.value=` + strconv.Quote(strconv.Itoa(index)) + `;f.submit();return true})()`
		// A navigation can interrupt evaluation; the verified returned state,
		// rather than a blind second submission, determines acceptance.
		evalErr := sessionEvaluate(ctx, expression, &submitted)
		if evalErr == nil && !submitted {
			return api.NjoynSnapshot{}, api.ErrNjoynSnapshotChanged
		}
		poll := `(()=>{const f=Array.from(document.forms).find(f=>f.elements.namedItem('pn'));const p=f&&f.elements.namedItem('pn');return document.readyState!=='loading'&&p&&p.value===` + strconv.Quote(strconv.Itoa(index)) + `})()`
		// Setting pn changes the old DOM immediately. Require a new document
		// response before accepting readiness, then validate its frame/loader.
		call, cancel := context.WithTimeout(ctx, time.Duration(navigationMS)*time.Millisecond)
		for call.Err() == nil {
			state.mu.Lock()
			loader := state.current.loaderID
			state.mu.Unlock()
			if loader != "" && loader != previousLoader {
				_ = sessionPoll(call, poll, time.Duration(navigationMS)*time.Millisecond)
				break
			}
			if sessionWait(call, 50*time.Millisecond) != nil {
				break
			}
		}
		cancel()
		if err := sessionWait(ctx, time.Duration(waitMS)*time.Millisecond); err != nil {
			return api.NjoynSnapshot{}, err
		}
		return njoynSnapshot(ctx, board, state)
	}
	return api.DiscoverNjoyn(ctx, controls, maxPages, attempts)
}

func sessionSnapshotRetry(err error) bool {
	return errors.Is(err, api.ErrBrassRingSnapshotChanged) && !errors.Is(err, errCleanupUnproved)
}
