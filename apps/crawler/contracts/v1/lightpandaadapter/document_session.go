package lightpandaadapter

import (
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"time"
)

// NewDocumentSessionRenderOnly grants only the bounded time of a validated
// sequential action pipeline and optional card collection. Navigation input
// validation, render-only capability checks and cleanup remain authoritative.
func NewDocumentSessionRenderOnly(runner Runner, pipeline []actions.Action, cards *actions.InlineCards) (*Adapter, error) {
	if len(pipeline) == 0 && cards == nil || len(pipeline) > 0 && !actions.Valid(pipeline) || cards != nil && !cards.Valid() {
		return nil, actions.ErrActions
	}
	adapter, err := NewNavigationRenderOnly(runner)
	if err != nil {
		return nil, err
	}
	adapter.sessionBudget = actions.Budget(pipeline)
	if cards != nil {
		adapter.sessionBudget += time.Duration(cards.TimeoutMS) * time.Millisecond
	}
	return adapter, nil
}
