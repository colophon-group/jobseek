package main

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"time"

	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
)

var errInlineCardsReserved = errors.New("inline card publisher reserved")

// Salesforce's synthetic-shadow library patches main-world querySelectorAll
// to hide its rendered controls. An isolated controller world sees the same
// DOM as the original browser locator, without changing publisher JavaScript.
func inlineCardValue(ctx context.Context, expression string, value any) error {
	return chromedp.Run(ctx, chromedp.ActionFunc(func(call context.Context) error {
		tree, err := page.GetFrameTree().Do(call)
		if err != nil || tree == nil || tree.Frame == nil {
			return errDocumentAction
		}
		world, err := page.CreateIsolatedWorld(tree.Frame.ID).WithWorldName("jobseek-inline-cards").Do(call)
		if err != nil {
			return errDocumentAction
		}
		result, exception, err := runtime.Evaluate(expression).WithContextID(world).WithAwaitPromise(true).WithReturnByValue(true).Do(call)
		if err != nil || exception != nil || result == nil || json.Unmarshal(result.Value, value) != nil {
			return errDocumentAction
		}
		return nil
	}))
}

func collectInlineCards(ctx context.Context, c actions.InlineCards, reload func() error, check func() (bool, error)) (string, []string, error) {
	if !c.Valid() || reload == nil || check == nil {
		return "", nil, actions.ErrActions
	}
	args, _ := json.Marshal([]string{c.ControlSelector, c.IdentitySelector, c.IdentityAttribute})
	read := func() ([]string, error) {
		var row struct {
			Count int
			IDs   []string `json:"ids"`
		}
		expression := `(()=>{const [controls,selector,attribute]=` + string(args) + `;return {Count:document.querySelectorAll(controls).length,ids:Array.from(document.querySelectorAll(selector)).map(e=>e.getAttribute(attribute))}})()`
		if inlineCardValue(ctx, expression, &row) != nil || row.Count < 1 || row.Count > int(c.Maximum) || len(row.IDs) != row.Count {
			return nil, errDocumentAction
		}
		return row.IDs, nil
	}
	var raw []string
	for {
		values, err := read()
		if err == nil {
			raw = values
			break
		}
		if interactionDelay(ctx, 50) != nil {
			return "", nil, errDocumentAction
		}
	}
	identities := make([]string, len(raw))
	seen := map[string]bool{}
	for i, v := range raw {
		id, ok := c.Identity(v)
		if !ok || seen[id] {
			return "", nil, errDocumentAction
		}
		seen[id] = true
		identities[i] = id
	}
	var out strings.Builder
	for i := range raw {
		if i > 0 {
			if reload() != nil {
				return "", nil, errDocumentAction
			}
			var current []string
			for {
				var err error
				current, err = read()
				if err == nil {
					break
				}
				if interactionDelay(ctx, 50) != nil {
					return "", nil, errDocumentAction
				}
			}
			if !reflect.DeepEqual(raw, current) {
				return "", nil, errDocumentAction
			}
		}
		reserved, err := check()
		if err != nil {
			return "", nil, err
		}
		if reserved {
			return "", nil, errInlineCardsReserved
		}
		input, _ := json.Marshal([]any{c.ControlSelector, i})
		var clicked bool
		if inlineCardValue(ctx, `(()=>{const [selector,index]=`+string(input)+`;const nodes=document.querySelectorAll(selector);if(!nodes[index])return false;nodes[index].click();return true})()`, &clicked) != nil || !clicked {
			return "", nil, errDocumentAction
		}
		selector, _ := json.Marshal([]any{c.ContentSelector, c.ContentIndex})
		var fragment string
		for {
			var value struct {
				Count   int
				HTML    string
				Visible bool
			}
			err := inlineCardValue(ctx, `(()=>{const [selector,index]=`+string(selector)+`;const nodes=document.querySelectorAll(selector);const node=nodes[index];return {Count:node?1:0,HTML:node?node.innerHTML:'',Visible:!!node&&getComputedStyle(node).display!=='none'&&getComputedStyle(node).visibility!=='hidden'}})()`, &value)
			if err != nil {
				return "", nil, errDocumentAction
			}
			if value.Count > 1 {
				return "", nil, errDocumentAction
			}
			if value.Count == 1 && value.Visible && value.HTML != "" {
				fragment = value.HTML
				break
			}
			if interactionDelay(ctx, 50) != nil {
				return "", nil, errDocumentAction
			}
		}
		reserved, err = check()
		if err != nil {
			return "", nil, err
		}
		if reserved {
			return "", nil, errInlineCardsReserved
		}
		lower := strings.ToLower(fragment)
		if strings.Contains(lower, "jobseek-inline-detail") || strings.Contains(lower, "__inline_detail_boundary__") {
			return "", nil, errDocumentAction
		}
		if out.Len()+len(fragment)+100 > 2000000 {
			return "", nil, errResourceLimit
		}
		out.WriteString("<jobseek-inline-detail>__inline_detail_boundary__")
		out.WriteString(fragment)
		out.WriteString("</jobseek-inline-detail>")
	}
	return out.String(), identities, nil
}

func inlineCardBudget(c *actions.InlineCards) time.Duration {
	if c == nil {
		return 0
	}
	return time.Duration(c.TimeoutMS) * time.Millisecond
}
