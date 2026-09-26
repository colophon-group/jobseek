package workable

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
)

var detailPathRE = regexp.MustCompile(`^/([A-Za-z0-9][A-Za-z0-9_-]{0,127})/j/([A-Za-z0-9_]+)/?$`)

type DetailResult struct {
	Content   *DetailContent `json:"content,omitempty"`
	Requests  int            `json:"requests"`
	Responses int            `json:"responses"`
	Bytes     int64          `json:"bytes"`
	Status    int            `json:"status"`
	FinalURL  string         `json:"final_url,omitempty"`
	ErrorKind string         `json:"error_kind,omitempty"`
	TDMPolicy string         `json:"tdm_policy,omitempty"`
	Error     string         `json:"error,omitempty"`
}

func detailEndpoints(sourceURL, override string) (string, string, error) {
	parsed, err := url.Parse(sourceURL)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() != "apply.workable.com" ||
		parsed.Port() != "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" ||
		parsed.EscapedPath() != parsed.Path {
		return "", "", errors.New("Workable detail URL is not canonical HTTPS")
	}
	parts := detailPathRE.FindStringSubmatch(parsed.Path)
	if parts == nil {
		return "", "", errors.New("Workable detail URL has an unsupported path")
	}
	slug := parts[1]
	if override != "" {
		if !validSlug(override) {
			return "", "", errors.New("Workable detail token override is invalid")
		}
		slug = override
	}
	base := "https://apply.workable.com/" + slug
	return "https://apply.workable.com/api/v2/accounts/" + slug + "/jobs/" + parts[2],
		base + "/jobs/view/" + parts[2] + ".md", nil
}

// FetchDetail performs the same one-shot API request and 429 Markdown fallback
// as the Python scraper. A non-200 response projects empty content for the
// existing scrape retry policy; a transport or projection error remains a
// failure so it cannot silently overwrite a good description.
func FetchDetail(ctx context.Context, sourceURL, override string) (DetailResult, error) {
	client := newClient()
	defer client.CloseIdleConnections()
	return fetchDetail(ctx, sourceURL, override, client)
}

func fetchDetail(ctx context.Context, sourceURL, override string, client requestDoer) (DetailResult, error) {
	apiURL, markdownURL, err := detailEndpoints(sourceURL, override)
	if err != nil {
		return DetailResult{}, err
	}
	accounting := FetchResult{}
	body, status, err := fetchBody(ctx, client, &accounting, http.MethodGet, apiURL, nil)
	if err != nil {
		return detailError(accounting, err)
	}
	if status == http.StatusTooManyRequests {
		markdown, fallbackStatus, err := fetchBody(ctx, client, &accounting, http.MethodGet, markdownURL, nil)
		if err != nil {
			return detailError(accounting, err)
		}
		if fallbackStatus == http.StatusOK {
			content := ProjectMarkdownDetail(markdown)
			result := detailAccounting(accounting)
			result.Content = &content
			return result, nil
		}
		return detailAccounting(accounting), nil
	}
	if status != http.StatusOK {
		return detailAccounting(accounting), nil
	}
	content, err := ProjectDetail(body)
	if err != nil {
		result := detailAccounting(accounting)
		result.ErrorKind = "invalid_payload"
		result.Error = err.Error()
		return result, err
	}
	result := detailAccounting(accounting)
	result.Content = &content
	return result, nil
}

func detailAccounting(accounting FetchResult) DetailResult {
	return DetailResult{
		Requests: accounting.Requests, Responses: accounting.Responses, Bytes: accounting.Bytes,
		Status: accounting.Status, FinalURL: accounting.FinalURL,
		ErrorKind: accounting.ErrorKind, TDMPolicy: accounting.TDMPolicy,
	}
}

func detailError(accounting FetchResult, err error) (DetailResult, error) {
	result := detailAccounting(accounting)
	result.Error = err.Error()
	if result.ErrorKind == "" {
		if result.Status == 0 {
			result.ErrorKind = "transport"
		} else {
			result.ErrorKind = "response"
		}
	}
	return result, fmt.Errorf("Workable detail fetch: %w", err)
}
