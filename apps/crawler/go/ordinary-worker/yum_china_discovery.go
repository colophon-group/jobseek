package worker

import (
	"context"
	"net/http"
	"net/http/cookiejar"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

// Keep the configured browser queue namespace and immutable source binding.
// This proven HTTP route reads the same complete public variable as the
// original browser expression, without loading unrelated scripts or widgets.
func fetchYumChinaPage(ctx context.Context, verified *http.Client) nextdataPage {
	result := nextdataPage{}
	client := *verified
	client.Jar, _ = cookiejar.New(nil)
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(endpoint string) ([]byte, error) {
		body, _, response, err := fetchLastHTTPOnce(ctx, &client, api.YumChinaResourceScope{}, api.Request{Method: "GET", URL: endpoint}, 4_000_000)
		result.response = response
		return body, err
	}
	page, err := fetch(api.YumChinaBoardURL)
	if err != nil {
		result.err = err
		return result
	}
	asset, err := api.YumChinaScriptURL(string(page))
	if err != nil {
		result.err = err
		return result
	}
	code, err := fetch(asset)
	if err != nil {
		result.err = err
		return result
	}
	result.document, result.err = api.ParseYumChinaJobs(string(code))
	if result.err == nil {
		value, err := api.Search(result.document.Value, "jobs")
		result.err = err
		if err == nil {
			result.items = value.([]any) // The parser constructs this array itself.
		}
	}
	return result
}
