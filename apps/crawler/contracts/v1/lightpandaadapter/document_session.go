package lightpandaadapter

import (
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"time"
)

// NewDocumentSessionRenderOnly grants only the bounded time of a validated
// sequential action pipeline and optional card collection. Navigation input
// validation, render-only capability checks and cleanup remain authoritative.
func NewDocumentSessionRenderOnly(runner Runner, pipeline []actions.Action, cards *actions.InlineCards) (*Adapter, error) {
	return newDocumentSession(runner, pipeline, cards, false)
}

// NewLargeDocumentSessionRenderOnly admits complete, bounded documents without
// requiring a synthetic action. It grants no evaluation or capture capability.
func NewLargeDocumentSessionRenderOnly(runner Runner, pipeline []actions.Action, cards *actions.InlineCards) (*Adapter, error) {
	return newDocumentSession(runner, pipeline, cards, true)
}

func newDocumentSession(runner Runner, pipeline []actions.Action, cards *actions.InlineCards, largeDocument bool) (*Adapter, error) {
	if len(pipeline) == 0 && cards == nil && !largeDocument || len(pipeline) > 0 && !actions.Valid(pipeline) || cards != nil && !cards.Valid() {
		return nil, actions.ErrActions
	}
	adapter, err := NewNavigationRenderOnly(runner)
	if err != nil {
		return nil, err
	}
	adapter.sessionBudget = actions.Budget(pipeline)
	adapter.largeDocument = largeDocument
	if cards != nil {
		adapter.sessionBudget += time.Duration(cards.TimeoutMS) * time.Millisecond
	}
	return adapter, nil
}

func documentHTMLLimit(largeDocument bool) uint64 {
	if largeDocument {
		return actions.LargeDocumentHTMLLimit
	}
	return HTMLPayloadLimit
}
