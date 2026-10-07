package main

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"time"
)

var errDocumentAction = errors.New("required browser action failed")

type documentActionExecutor func(context.Context, actions.Action) error

func runDocumentActions(ctx context.Context, pipeline []actions.Action, execute documentActionExecutor, check func() (bool, error)) error {
	if !actions.Valid(pipeline) {
		return actions.ErrActions
	}
	for _, action := range pipeline {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		blocked, err := check()
		if err != nil {
			return err
		}
		if blocked {
			return nil
		}
		actionCtx, cancel := context.WithTimeout(ctx, time.Duration(action.TimeoutMS)*time.Millisecond)
		err = execute(actionCtx, action)
		cancel()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err != nil && action.Required {
			return errDocumentAction
		}
	}
	_, err := check()
	return err
}

func executeDocumentAction(ctx context.Context, action actions.Action) error {
	if action.Kind == "wait" {
		timer := time.NewTimer(time.Duration(action.Milliseconds * float64(time.Millisecond)))
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
			return nil
		}
	}
	if action.Kind != "evaluate" {
		return actions.ErrActions
	}
	source, _ := json.Marshal(action.Script)
	// Playwright invokes a function expression and awaits a returned Promise.
	// The result is discarded; only the recaptured document crosses the service.
	expression := `(async () => { const source = ` + string(source) + `; let compiled; try { compiled = new Function('return ('+source+'\n)'); } catch (_) {} let value = compiled ? compiled() : (0,eval)(source); if (typeof value === 'function') value = value(); await value; return null; })()`
	return chromedp.Run(ctx, chromedp.ActionFunc(func(actionCtx context.Context) error {
		_, exception, err := runtime.Evaluate(expression).WithAwaitPromise(true).WithReturnByValue(true).Do(actionCtx)
		if err != nil || exception != nil {
			return errDocumentAction
		}
		return nil
	}))
}
