package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/chromedp/chromedp"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

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
