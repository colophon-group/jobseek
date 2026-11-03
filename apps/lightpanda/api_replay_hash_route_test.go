package main

import (
	"context"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	"testing"
)

func TestHashRouteTaskNavigatesRootAndRetainsCanonicalBoard(t *testing.T) {
	board := "https://careers.trip.com/#/jobList"
	metadata := `{"browser":true,"api_url":"https://careers.trip.com/api/oversea/getOverseaJobAd","method":"POST","json_path":"jobs","url_template":"https://careers.trip.com/#/job-detail?fromId={id}","fields":{"title":"title"}}`
	task, err := newAPIReplayTask(board, metadata, func(context.Context, api.Fetch, bool) error { return nil })
	if err != nil || task.URL != "https://careers.trip.com/" || task.APIReplay.boardURL != board || task.APIReplay.options.Inventory.BoardURL != board || validateTask(task) != nil {
		t.Fatal("canonical identity or validated root task changed", err)
	}
	for _, navigation := range []string{"https://foreign.example/", "https://careers.trip.com/other", "https://careers.trip.com/#/other"} {
		changed := task
		changed.URL = navigation
		if validateTask(changed) == nil {
			t.Fatal("unqualified navigation acquired canonical task authority", navigation)
		}
	}
}
