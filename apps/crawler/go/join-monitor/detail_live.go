package join

import (
	"context"
	"errors"
	"net/url"
	"strings"
)

type DetailResult struct {
	FetchResult
	Content map[string]any `json:"content"`
}

func ValidateDetailURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Port() != "" || u.Fragment != "" ||
		(u.Hostname() != "join.com" && u.Hostname() != "www.join.com") {
		return errors.New("unsupported JOIN detail URL")
	}
	parts := strings.SplitN(strings.TrimPrefix(u.Path, "/"), "/", 3)
	if len(parts) != 3 || parts[0] != "companies" || !slugRE.MatchString(parts[1]) || parts[2] == "" {
		return errors.New("unsupported JOIN detail path")
	}
	return nil
}

func fetchDetail(ctx context.Context, client requestDoer, request DetailRequest) (DetailResult, error) {
	result := DetailResult{Content: emptyContent()}
	if err := ValidateDetailURL(request.URL); err != nil {
		return result, err
	}
	fields, err := ValidateDetailConfig(request.Config)
	if err != nil {
		return result, err
	}
	if len(fields) == 0 {
		return result, nil
	} // Python returns before HTTP too.
	body, err := fetchPage(ctx, client, request.URL, &result.FetchResult)
	if err != nil {
		return result, err
	}
	if result.Status != 200 {
		return result, nil
	} // Preserve empty extraction, not a tombstone.
	result.Content, err = ParseDetail(body, request.Config)
	if err == nil {
		captureText("/tmp", request.URL, body, true)
	}
	return result, err
}

func FetchDetail(ctx context.Context, request DetailRequest) (DetailResult, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return fetchDetail(ctx, client, request)
}

// FetchDetailWithClient retains the existing parser, bounded redirects and
// publisher policy while the native worker supplies its verified transport.
func FetchDetailWithClient(ctx context.Context, request DetailRequest, client requestDoer) (DetailResult, error) {
	return fetchDetail(ctx, client, request)
}
