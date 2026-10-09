package main

import (
	"context"
	"fmt"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/chromedp/chromedp"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func newBrassRingBrowserTask(board, metadata string, converse func(context.Context, api.BrassRingPageLoader) error) (Task, error) {
	_, options, err := api.BrassRingBrowserOptions(board, metadata)
	if err != nil || converse == nil {
		return Task{}, errReplayCapture
	}
	wait := map[string]runtimev1.WaitCondition{"commit": 1, "domcontentloaded": 2, "load": 3, "networkidle": 4}[options.Wait]
	navigation := &navigationOptions{wait: wait, timeout: time.Duration(options.TimeoutMS) * time.Millisecond, transportRetries: uint32(options.TransportRetries)}
	if options.WaitFallback != "" && options.WaitFallback != options.Wait {
		navigation.fallback = &navigationOptions{wait: map[string]runtimev1.WaitCondition{"commit": 1, "domcontentloaded": 2, "load": 3, "networkidle": 4}[options.WaitFallback], timeout: min(5*time.Second, navigation.timeout)}
	}
	return Task{URL: board, Navigation: navigation, APIReplay: &apiReplayTask{boardURL: board, options: options, brassRingConverse: converse, nativeMetadata: metadata}}, nil
}

func executeBrassRingConversation(ctx context.Context, task *apiReplayTask, finalURL string) error {
	expected, err := api.BrassRingBoardFromURL(task.boardURL)
	actual, parseErr := api.BrassRingBoardFromURL(finalURL)
	board, _ := url.Parse(task.boardURL)
	final, _ := url.Parse(finalURL)
	if err != nil || parseErr != nil || expected != actual || board.Scheme != final.Scheme || board.Host != final.Host || task.brassRingConverse == nil {
		return errReplayCapture
	}
	var mu sync.Mutex
	open, sorted, current := true, false, 0
	defer func() { mu.Lock(); open = false; mu.Unlock() }()
	return task.brassRingConverse(ctx, func(call context.Context, page int, stable bool) (*api.Document, error) {
		mu.Lock()
		defer mu.Unlock()
		if !open || ctx.Err() != nil || call.Err() != nil || stable && (page != 1 || current != 1 || sorted) || !stable && (page != current+1 || page > 1 && !sorted) {
			return nil, errReplayCapture
		}
		operation, cancel := context.WithCancel(call)
		stop := context.AfterFunc(ctx, cancel)
		defer cancel()
		defer stop()
		document, err := loadBrassRingBrowserPage(operation, task.boardURL, task.nativeMetadata, page, stable)
		if err == nil {
			current = page
			sorted = sorted || stable
		}
		return document, err
	})
}

// The caller owns a fresh held target. Register the response capture before
// clicking, then await Angular's committed page before another page can run.
// This driver alone does not admit a protocol provider or a queue profile.
func loadBrassRingBrowserPage(ctx context.Context, board, metadata string, page int, sorted bool) (*api.Document, error) {
	_, options, err := api.BrassRingBrowserOptions(board, metadata)
	if err != nil || page < 1 || page > 50000 || sorted && page != 1 {
		return nil, errReplayCapture
	}
	if page > 1 || sorted {
		options.Inventory.Endpoint = strings.TrimSuffix(options.Inventory.Endpoint, "MatchedJobs") + "ProcessSortAndShowMoreJobs"
	}
	capture, err := newReplayCapture(options)
	if err != nil {
		return nil, err
	}
	capture.requireStatus = 200
	call, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	defer capture.erase()
	chromedp.ListenTarget(call, capture.observe)
	if sorted {
		var opened bool
		err = chromedp.Run(call, chromedp.Evaluate(`(()=>{const options=Array.from(document.querySelectorAll('#sortBy option'));const button=document.querySelector('#sortBy-button');if(!button||!options.some(x=>x.value==='1'))return false;button.click();return true})()`, &opened))
		if err != nil {
			return nil, brassRingCommandError(call)
		}
		if !opened {
			return nil, api.ErrBrassRingSnapshot
		}
		ready := `(()=>{const options=Array.from(document.querySelectorAll('#sortBy option'));const index=options.findIndex(x=>x.value==='1');return index>=0&&!!document.querySelector('#sortBy-menu li:nth-child('+(index+1)+')')})()`
		if err = waitBrassRingDOM(call, ready); err != nil {
			return nil, err
		}
		var clicked bool
		err = chromedp.Run(call, chromedp.Evaluate(`(()=>{const options=Array.from(document.querySelectorAll('#sortBy option'));const index=options.findIndex(x=>x.value==='1');const item=document.querySelector('#sortBy-menu li:nth-child('+(index+1)+')');if(index<0||!item)return false;item.click();return true})()`, &clicked))
		if err != nil {
			return nil, brassRingCommandError(call)
		}
		if !clicked {
			return nil, api.ErrBrassRingSnapshot
		}
	} else {
		selector := `#clearResumeJobsBtn`
		if page > 1 {
			selector = `button[title="Next Page"]:not(.disabled-link)`
		}
		ready := fmt.Sprintf(`(()=>{const node=document.querySelector(%q);return !!node&&!node.disabled})()`, selector)
		if page == 1 {
			if err = waitBrassRingDOM(call, ready); err != nil {
				return nil, err
			}
		}
		var clicked bool
		err = chromedp.Run(call, chromedp.Evaluate(fmt.Sprintf(`(()=>{const node=document.querySelector(%q);if(!node||node.disabled)return false;node.click();return true})()`, selector), &clicked))
		if err != nil {
			return nil, brassRingCommandError(call)
		}
		if !clicked {
			return nil, api.ErrBrassRingSnapshot
		}
	}
	ticker := time.NewTicker(25 * time.Millisecond)
	defer ticker.Stop()
	for {
		capture.mu.Lock()
		failure := capture.failure
		capture.mu.Unlock()
		if failure != nil {
			return nil, failure
		}
		if len(capture.completed) > 0 {
			break
		}
		select {
		case <-call.Done():
			return nil, call.Err()
		case <-ticker.C:
		}
	}
	exchanges, err := capture.exchanges(call, readReplayResponseBody)
	if err != nil {
		return nil, err
	}
	if len(exchanges) != 1 {
		return nil, errReplayCapture
	}
	headers, document, matched, err := api.SelectBrowserReplayExchange(options, exchanges)
	clear(headers)
	if err != nil || !matched || document == nil {
		return nil, errReplayCapture
	}
	total, rows, err := api.BrassRingPage(document)
	if err != nil {
		return nil, err
	}
	if page > 1 || total > len(rows) {
		expression := fmt.Sprintf(`(()=>{const current=document.querySelector('.pagewise-pagination[aria-current="page"]');return !!current&&current.textContent.trim()==='%d'})()`, page)
		if err = waitBrassRingDOM(call, expression); err != nil {
			return nil, err
		}
	}
	return document, nil
}

func brassRingCommandError(ctx context.Context) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return errReplayCapture
}

func waitBrassRingDOM(ctx context.Context, expression string) error {
	ticker := time.NewTicker(50 * time.Millisecond)
	defer ticker.Stop()
	for {
		var ready bool
		if err := chromedp.Run(ctx, chromedp.Evaluate(expression, &ready)); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return errReplayCapture
		}
		if ready {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}
