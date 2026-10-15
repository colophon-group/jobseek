package main

import (
	"context"
	"encoding/json"
	"sort"
	"sync"
	"time"

	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
)

// Selector strings remain data. Interpret CSS, quoted has-text and a terminal
// visible predicate. Visibility keeps Playwright's nonempty-box/style rule;
// opacity zero remains visible (https://playwright.dev/docs/actionability#visible).
const interactionPickerJS = `function elementVisible(n) {
 const style=getComputedStyle(n);
 if(style.display==='contents'){
  return Array.from(n.childNodes).some(child=>{
   if(child.nodeType===Node.ELEMENT_NODE)return elementVisible(child);
   if(child.nodeType!==Node.TEXT_NODE)return false;
   if(parseFloat(style.fontSize)===0)return false;
   if(/^[ \t\n\r\f]*$/.test(child.textContent||'')&&!['pre','pre-wrap','break-spaces'].includes(style.whiteSpace))return false;
   const range=document.createRange();range.selectNodeContents(child);const rect=range.getBoundingClientRect();
   return rect.width>0&&rect.height>0;
  });
 }
 const rect=n.getBoundingClientRect();
 return style.visibility!=='hidden'&&style.visibility!=='collapse'&&style.display!=='none'&&rect.width>0&&rect.height>0;
}
function pick(selector) {
 const texts=[];
 const visible=selector.endsWith(':visible');
 const base=visible?selector.slice(0,-8):selector;
 const css=base.replace(/:has-text\(\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')\s*\)/g,(_,raw)=>{
  let text=raw[0]==='"'?JSON.parse(raw):raw.slice(1,-1).replace(/\\(['"\\])/g,'$1');
  texts.push(text.replace(/\s+/g,' ').trim().toLowerCase());return '';
 });
 if(css.includes(':has-text(')||css.includes(':visible'))throw new Error('unsupported selector');
 return Array.from(document.querySelectorAll(css)).find(n=>{
  if(visible&&!elementVisible(n))return false;
  return texts.every(t=>(n.textContent||'').replace(/\s+/g,' ').trim().toLowerCase().includes(t));
 })||null;
}`

func interactionValue(ctx context.Context, expression string, value any) error {
	return chromedp.Run(ctx, chromedp.ActionFunc(func(call context.Context) error {
		result, exception, err := runtime.Evaluate(expression).WithAwaitPromise(true).WithReturnByValue(true).Do(call)
		if err != nil || exception != nil || result == nil || json.Unmarshal(result.Value, value) != nil {
			return errDocumentAction
		}
		return nil
	}))
}

func interactionDelay(ctx context.Context, milliseconds float64) error {
	timer := time.NewTimer(time.Duration(milliseconds * float64(time.Millisecond)))
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// Keep the controller outside the page so an anchor navigation cannot destroy
// its loop. Wait for an initiated main-frame load before inspecting the next
// document; ordinary SPA clicks continue without a main-frame load.
func interactionClick(ctx context.Context, selector string) error {
	var root string
	if err := chromedp.Run(ctx, chromedp.ActionFunc(func(call context.Context) error {
		tree, err := page.GetFrameTree().Do(call)
		if err != nil || tree == nil || tree.Frame == nil {
			return errDocumentAction
		}
		root = string(tree.Frame.ID)
		return nil
	})); err != nil {
		return err
	}
	watch, cancel := context.WithCancel(ctx)
	defer cancel()
	var mu sync.Mutex
	loading := false
	changed := make(chan struct{}, 1)
	chromedp.ListenTarget(watch, func(event any) {
		mu.Lock()
		defer mu.Unlock()
		switch event := event.(type) {
		case *page.EventFrameStartedLoading:
			if string(event.FrameID) == root {
				loading = true
			}
		case *page.EventFrameNavigated:
			if event.Frame.ParentID == "" {
				root = string(event.Frame.ID)
				loading = true
			}
		case *page.EventFrameStoppedLoading:
			if string(event.FrameID) == root {
				loading = false
			}
		default:
			return
		}
		select {
		case changed <- struct{}{}:
		default:
		}
	})
	encoded, _ := json.Marshal(selector)
	var clicked bool
	if err := interactionValue(ctx, `(()=>{`+interactionPickerJS+`;const node=pick(`+string(encoded)+`);if(!node)return false;node.dispatchEvent(new MouseEvent('mouseover',{bubbles:true,cancelable:true,view:window}));node.dispatchEvent(new MouseEvent('mouseenter',{bubbles:false,cancelable:false,view:window}));node.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,cancelable:true,view:window}));const focus=node.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,button:0,buttons:1,view:window}));if(focus&&typeof node.focus==='function')node.focus();node.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,button:0,buttons:0,view:window}));node.click();return true})()`, &clicked); err != nil || !clicked {
		return errDocumentAction
	}
	if err := interactionDelay(ctx, 100); err != nil {
		return err
	}
	for {
		mu.Lock()
		active := loading
		mu.Unlock()
		if !active {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-changed:
		}
	}
}

func interactionExists(ctx context.Context, selector string) (bool, error) {
	encoded, _ := json.Marshal(selector)
	var present bool
	err := interactionValue(ctx, `(()=>{`+interactionPickerJS+`;return pick(`+string(encoded)+`)!==null})()`, &present)
	return present, err
}

func interactionWaitState(ctx context.Context, selector, state string) (bool, error) {
	encoded, _ := json.Marshal(selector)
	predicate := "node!==null"
	switch state {
	case "detached":
		predicate = "node===null"
	case "visible":
		predicate = "node!==null&&elementVisible(node)"
	case "hidden":
		predicate = "node===null||!elementVisible(node)"
	}
	var ready bool
	err := interactionValue(ctx, `(()=>{`+interactionPickerJS+`;const node=pick(`+string(encoded)+`);return `+predicate+`})()`, &ready)
	return ready, err
}

func interactionPolicy(check func() (bool, error)) (bool, error) {
	if check == nil {
		return false, errDocumentAction
	}
	return check()
}

func executeDocumentInteraction(ctx context.Context, action actions.Action, check func() (bool, error)) error {
	switch action.Kind {
	case "click":
		return interactionClick(ctx, action.Selector)
	case "wait_for":
		for {
			ready, err := interactionWaitState(ctx, action.Selector, action.State)
			if err != nil || ready {
				return err
			}
			if err := interactionDelay(ctx, 50); err != nil {
				return err
			}
		}
	case "repeat":
		for n := uint64(0); n < action.Maximum; n++ {
			blocked, err := interactionPolicy(check)
			if err != nil || blocked {
				return err
			}
			present, err := interactionExists(ctx, action.Selector)
			if err != nil || !present {
				return err
			}
			var before, after int
			if interactionValue(ctx, `document.querySelectorAll('a[href]').length`, &before) != nil || interactionClick(ctx, action.Selector) != nil || interactionDelay(ctx, action.WaitMS) != nil {
				return errDocumentAction
			}
			blocked, err = interactionPolicy(check)
			if err != nil || blocked {
				return err
			}
			if err := interactionValue(ctx, `document.querySelectorAll('a[href]').length`, &after); err != nil {
				return err
			}
			if after <= before {
				break
			}
		}
		return nil
	case "paginate_collect":
		return collectInteractionPages(ctx, action, check)
	}
	return actions.ErrActions
}

func collectInteractionPages(ctx context.Context, action actions.Action, check func() (bool, error)) error {
	if action.PageSizeSelector != "" && action.PageSize != "" {
		input, _ := json.Marshal([]string{action.PageSizeSelector, action.PageSize})
		var selected bool
		expression := `(()=>{const [selector,value]=` + string(input) + `;const node=document.querySelector(selector);if(!node)return false;node.value=value;node.dispatchEvent(new Event('change'));if(typeof juic!=='undefined'&&node.id)juic.fire(node.id,'_onChange',new Event('change'));return true})()`
		if interactionValue(ctx, expression, &selected) != nil || interactionDelay(ctx, action.WaitMS) != nil {
			return errDocumentAction
		}
	}
	links := map[string]bool{}
	collect := func() (int, error) {
		var values []string
		if err := interactionValue(ctx, `Array.from(document.querySelectorAll('a[href]')).map(a=>a.href).filter(href=>href.startsWith('http'))`, &values); err != nil {
			return 0, err
		}
		before := len(links)
		for _, value := range values {
			links[value] = true
		}
		if len(links) > 50000 {
			return 0, errResourceLimit
		}
		return len(links) - before, nil
	}
	if _, err := collect(); err != nil {
		return err
	}
	for n := uint64(0); n < action.MaxPages; n++ {
		blocked, err := interactionPolicy(check)
		if err != nil || blocked {
			return err
		}
		present, err := interactionExists(ctx, action.NextSelector)
		if err != nil || !present {
			if err != nil {
				return err
			}
			break
		}
		if interactionClick(ctx, action.NextSelector) != nil || interactionDelay(ctx, action.WaitMS) != nil {
			return errDocumentAction
		}
		blocked, err = interactionPolicy(check)
		if err != nil || blocked {
			return err
		}
		added, err := collect()
		if err != nil || added == 0 {
			return errDocumentAction
		}
		if n+1 == action.MaxPages {
			present, err := interactionExists(ctx, action.NextSelector)
			if err != nil || present {
				return errDocumentAction
			}
		}
	}
	ordered := make([]string, 0, len(links))
	for value := range links {
		ordered = append(ordered, value)
	}
	sort.Strings(ordered)
	encoded, _ := json.Marshal(ordered)
	var injected bool
	expression := `(()=>{const box=document.createElement('div');box.style.display='none';for(const href of ` + string(encoded) + `){const a=document.createElement('a');a.href=href;box.appendChild(a)}document.body.appendChild(box);return true})()`
	return interactionValue(ctx, expression, &injected)
}
