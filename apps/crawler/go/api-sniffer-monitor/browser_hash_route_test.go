package apisniffer

import (
	"context"
	"strings"
	"testing"
)

const tripHashTestMetadata = `{"browser":true,"api_url":"https://careers.trip.com/api/oversea/getOverseaJobAd","method":"POST","json_path":"jobs","url_template":"https://careers.trip.com/#/job-detail?fromId={id}&atsApiType=Moka_Overseas","fields":{"title":"title"}}`

func TestTripHashListingPreservesCanonicalIdentityAndJobFragments(t *testing.T) {
	board := "https://careers.trip.com/#/jobList"
	o, err := BrowserReplayOptionsFromMetadata(board, tripHashTestMetadata)
	if err != nil || o.BootstrapURL != "https://careers.trip.com/" || o.Inventory.BoardURL != board {
		t.Fatal("canonical identity or source bootstrap changed", err)
	}
	calls := 0
	got, err := DiscoverBrowserReplay(context.Background(), o, func(_ context.Context, request Request) (*Document, error) {
		calls++
		if request.Method != "POST" || request.URL != o.Inventory.Endpoint {
			t.Fatal("explicit API request changed")
		}
		return Decode([]byte(`{"jobs":[{"id":"one","title":"Engineer"}]}`))
	}, PythonJoinURL, false)
	if err != nil || calls != 1 || got.Truncated || len(got.Jobs) != 1 || got.Jobs[0].URL != "https://careers.trip.com/#/job-detail?fromId=one&atsApiType=Moka_Overseas" {
		t.Fatal("hash-routed inventory changed", err)
	}
}

func TestTripHashBootstrapRejectsUnqualifiedAliases(t *testing.T) {
	for _, c := range []struct{ board, metadata string }{
		{"https://careers.trip.com/#/other", tripHashTestMetadata},
		{"https://foreign.example/#/jobList", tripHashTestMetadata},
		{"https://careers.trip.com/#/jobList", strings.Replace(tripHashTestMetadata, "/api/oversea/getOverseaJobAd", "/api/unproved", 1)},
		{"https://careers.trip.com/#/jobList", strings.Replace(tripHashTestMetadata, `"POST"`, `"GET"`, 1)},
		{"https://careers.trip.com/#/jobList", strings.Replace(tripHashTestMetadata, `"browser":true`, `"browser":true,"bootstrap_url":"https://foreign.example/"`, 1)},
	} {
		if _, err := BrowserReplayOptionsFromMetadata(c.board, c.metadata); err == nil {
			t.Fatal("unqualified alias acquired a browser profile")
		}
	}
}
