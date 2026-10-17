package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

var errGitHubUnknownCreate = errors.New("GitHub create outcome is unknown")

type githubRateLimitError struct{ RetryAfter, ResetAt *int64 }

func (e *githubRateLimitError) Error() string { return "GitHub rate limited request" }
func (e *githubRateLimitError) retryAt(now int64) *int64 {
	var result *int64
	if e.ResetAt != nil {
		v := *e.ResetAt
		result = &v
	}
	if e.RetryAfter != nil {
		v := now + max(0, *e.RetryAfter)
		if result == nil || v > *result {
			result = &v
		}
	}
	return result
}

type githubCreatedIssue struct {
	Number int
	URL    string
}
type githubExistingIssue struct {
	Number                  int
	State, Title, Body, URL string
}
type inventoryGitHubClient struct {
	Client                   inventoryHTTPClient
	Repo, Token              string
	RateReserve              int64
	Pace                     time.Duration
	RateRemaining, RateReset *int64
	Sleep                    func(context.Context, time.Duration) error
}

func sleepInventoryContext(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
func newInventoryGitHubClient(client inventoryHTTPClient, repo, token string, reserve int64) (*inventoryGitHubClient, error) {
	parts := strings.Split(repo, "/")
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" || token == "" {
		return nil, fmt.Errorf("GitHub repository and token required")
	}
	return &inventoryGitHubClient{Client: client, Repo: repo, Token: token, RateReserve: reserve, Pace: 250 * time.Millisecond, Sleep: sleepInventoryContext}, nil
}
func readInventoryGitHubToken(envName, path string) (string, error) {
	if value := os.Getenv(envName); value != "" {
		return value, nil
	}
	if path == "" {
		return "", fmt.Errorf("GitHub token environment or token file required")
	}
	info, e := os.Lstat(path)
	if e != nil || !info.Mode().IsRegular() || info.Size() > 4096 {
		return "", fmt.Errorf("GitHub token file must be a small regular non-symlink file")
	}
	raw, e := boundedInventoryFile(path, 4096)
	if e != nil || !utf8.Valid(raw) {
		return "", fmt.Errorf("GitHub token file unreadable")
	}
	value := strings.TrimSuffix(strings.TrimSuffix(string(raw), "\n"), "\r")
	if value == "" || value != pythonInventoryStrip(value) || strings.ContainsAny(value, "\r\n\v\f\x1c\x1d\x1e\u0085\u2028\u2029") {
		return "", fmt.Errorf("GitHub token file must contain exactly one non-empty line")
	}
	return value, nil
}
func optionalInventoryInt(s string) *int64 {
	v, e := strconv.ParseInt(strings.TrimSpace(s), 10, 64)
	if e != nil {
		return nil
	}
	return &v
}
func (c *inventoryGitHubClient) ratePreflight() error {
	if c.RateRemaining != nil && *c.RateRemaining <= c.RateReserve {
		return &githubRateLimitError{ResetAt: c.RateReset}
	}
	return nil
}
func (c *inventoryGitHubClient) updateRate(response *http.Response) {
	if v := optionalInventoryInt(response.Header.Get("X-RateLimit-Remaining")); v != nil {
		c.RateRemaining = v
	}
	if v := optionalInventoryInt(response.Header.Get("X-RateLimit-Reset")); v != nil {
		c.RateReset = v
	}
}
func (c *inventoryGitHubClient) checkResponse(response *http.Response, body []byte) error {
	c.updateRate(response)
	limited := response.StatusCode == 429
	if response.StatusCode == 403 {
		_, retry := response.Header["Retry-After"]
		zero := optionalInventoryInt(response.Header.Get("X-RateLimit-Remaining"))
		limited = retry || zero != nil && *zero == 0
		var payload map[string]any
		if json.Unmarshal(body, &payload) == nil {
			if message, ok := payload["message"].(string); ok {
				lower := unicodeFold(message)
				for _, phrase := range []string{"secondary rate limit", "api rate limit exceeded", "abuse detection mechanism"} {
					limited = limited || strings.Contains(lower, phrase)
				}
			}
		}
	}
	if limited {
		return &githubRateLimitError{RetryAfter: optionalInventoryInt(response.Header.Get("Retry-After")), ResetAt: c.RateReset}
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("GitHub request failed with HTTP %d", response.StatusCode)
	}
	return nil
}
func (c *inventoryGitHubClient) request(ctx context.Context, method, endpoint string, params url.Values, payload any) (*http.Response, []byte, error) {
	var body io.Reader
	if payload != nil {
		raw, e := json.Marshal(payload)
		if e != nil {
			return nil, nil, e
		}
		body = bytes.NewReader(raw)
	}
	address := "https://api.github.com/repos/" + c.Repo + "/" + endpoint
	if len(params) > 0 {
		address += "?" + params.Encode()
	}
	request, e := http.NewRequestWithContext(ctx, method, address, body)
	if e != nil {
		return nil, nil, fmt.Errorf("GitHub request invalid")
	}
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("Authorization", "Bearer "+c.Token)
	request.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, e := c.Client.Do(request)
	if e != nil {
		return nil, nil, fmt.Errorf("GitHub transport request failed")
	}
	defer response.Body.Close()
	raw, e := io.ReadAll(io.LimitReader(response.Body, 32*1024*1024+1))
	if e != nil || len(raw) > 32*1024*1024 {
		return response, nil, fmt.Errorf("GitHub response unreadable or exceeds byte limit")
	}
	return response, raw, nil
}
func (c *inventoryGitHubClient) listPages(ctx context.Context, endpoint string, params url.Values, skipNonObjects bool) ([]map[string]any, error) {
	out := []map[string]any{}
	for page := 1; page <= 100; page++ {
		if e := c.ratePreflight(); e != nil {
			return nil, e
		}
		query := url.Values{}
		for key, values := range params {
			query[key] = append([]string{}, values...)
		}
		query.Set("per_page", "100")
		query.Set("page", strconv.Itoa(page))
		response, body, e := c.request(ctx, http.MethodGet, endpoint, query, nil)
		if e != nil {
			return nil, e
		}
		if e = c.checkResponse(response, body); e != nil {
			return nil, e
		}
		if e = c.Sleep(ctx, c.Pace); e != nil {
			return nil, e
		}
		var raw []any
		decoder := json.NewDecoder(bytes.NewReader(body))
		decoder.UseNumber()
		if e = decoder.Decode(&raw); e != nil || raw == nil {
			return nil, fmt.Errorf("GitHub response is not a list")
		}
		for _, v := range raw {
			m, ok := v.(map[string]any)
			if !ok {
				if skipNonObjects {
					continue
				}
				return nil, fmt.Errorf("GitHub response is not an object list")
			}
			out = append(out, m)
		}
		if len(raw) < 100 {
			return out, nil
		}
	}
	return nil, fmt.Errorf("GitHub pagination exceeded 100 pages")
}
func githubInventoryField(raw map[string]any, key string) (string, error) {
	v, ok := raw[key]
	if !ok {
		return "", fmt.Errorf("GitHub item has invalid shape")
	}
	if v == nil {
		return "None", nil
	}
	if s, ok := v.(string); ok {
		return s, nil
	}
	return fmt.Sprint(v), nil
}
func githubInventoryNumber(raw map[string]any) (int, error) {
	v, e := impactInt(raw, "number")
	if e != nil {
		return 0, fmt.Errorf("GitHub item number invalid")
	}
	return v, nil
}
func (c *inventoryGitHubClient) listCandidateWorkItems(ctx context.Context) ([]githubWorkItem, error) {
	issues, e := c.listPages(ctx, "issues", url.Values{"state": {"all"}, "labels": {"company-request"}}, false)
	if e != nil {
		return nil, e
	}
	prs, e := c.listPages(ctx, "pulls", url.Values{"state": {"open"}}, false)
	if e != nil {
		return nil, e
	}
	out := []githubWorkItem{}
	for _, group := range []struct {
		kind string
		rows []map[string]any
	}{{"issue", issues}, {"pr", prs}} {
		for _, raw := range group.rows {
			if group.kind == "issue" {
				if _, pr := raw["pull_request"]; pr {
					continue
				}
			}
			number, e := githubInventoryNumber(raw)
			if e != nil {
				return nil, e
			}
			state, e := githubInventoryField(raw, "state")
			if e != nil {
				return nil, e
			}
			title, e := githubInventoryField(raw, "title")
			if e != nil {
				return nil, e
			}
			address, e := githubInventoryField(raw, "html_url")
			if e != nil {
				return nil, e
			}
			body, _ := raw["body"].(string)
			labels := []string{}
			if group.kind == "issue" {
				set := map[string]bool{}
				if list, ok := raw["labels"].([]any); ok {
					for _, v := range list {
						if m, ok := v.(map[string]any); ok {
							if name, ok := m["name"].(string); ok {
								set[name] = true
							}
						}
					}
				}
				labels = sortedInventoryKeys(set)
			}
			var created *string
			if value, ok := raw["created_at"].(string); ok {
				created = &value
			}
			out = append(out, githubWorkItem{group.kind, number, state, title, body, address, labels, created})
		}
	}
	return out, nil
}
func (c *inventoryGitHubClient) listRecentClaims(ctx context.Context, since time.Time) ([]githubClaim, error) {
	raw, e := c.listPages(ctx, "issues/comments", url.Values{"since": {since.UTC().Format(time.RFC3339Nano)}, "sort": {"created"}, "direction": {"asc"}}, false)
	if e != nil {
		return nil, e
	}
	claims := []githubClaim{}
	for _, m := range raw {
		body, ok := m["body"].(string)
		if !ok || !strings.HasPrefix(body, "<!-- ws-claim -->") {
			continue
		}
		address, aok := m["issue_url"].(string)
		at, tok := m["created_at"].(string)
		if !aok || !tok {
			return nil, fmt.Errorf("GitHub claim shape invalid")
		}
		parts := strings.Split(strings.TrimRight(address, "/"), "/")
		n, e := strconv.Atoi(parts[len(parts)-1])
		if e != nil || n <= 0 {
			return nil, fmt.Errorf("GitHub claim issue number invalid")
		}
		claims = append(claims, githubClaim{n, body, at})
	}
	return claims, nil
}
func (c *inventoryGitHubClient) countOpenCompanyRequests(ctx context.Context) (int, error) {
	raw, e := c.listPages(ctx, "issues", url.Values{"state": {"open"}, "labels": {"company-request"}}, false)
	if e != nil {
		return 0, e
	}
	n := 0
	for _, m := range raw {
		if _, pr := m["pull_request"]; !pr {
			n++
		}
	}
	return n, nil
}
func (c *inventoryGitHubClient) createIssue(ctx context.Context, title, body string, labels []string) (githubCreatedIssue, error) {
	var empty githubCreatedIssue
	if e := c.ratePreflight(); e != nil {
		return empty, e
	}
	response, raw, e := c.request(ctx, http.MethodPost, "issues", nil, map[string]any{"title": title, "body": body, "labels": labels})
	if e != nil {
		return empty, errGitHubUnknownCreate
	}
	if response.StatusCode >= 500 || response.StatusCode == 408 || response.StatusCode == 425 {
		c.updateRate(response)
		return empty, errGitHubUnknownCreate
	}
	if e = c.checkResponse(response, raw); e != nil {
		return empty, e
	}
	m, e := decodeJSONMap(raw)
	if e != nil {
		return empty, errGitHubUnknownCreate
	}
	number, e := impactInt(m, "number")
	address, ok := m["html_url"].(string)
	if e != nil || number <= 0 || !ok || pythonInventoryStrip(address) == "" {
		return empty, errGitHubUnknownCreate
	}
	if e = c.Sleep(ctx, max(time.Second, c.Pace)); e != nil {
		return empty, errGitHubUnknownCreate
	}
	return githubCreatedIssue{number, address}, nil
}

var inventorySupportMarker = regexp.MustCompile(`<!-- ats-inventory-support:family=([a-z0-9][a-z0-9_]{0,63}) -->`)

func (c *inventoryGitHubClient) listSupportIssues(ctx context.Context) ([]githubExistingIssue, error) {
	raw, e := c.listPages(ctx, "issues", url.Values{"state": {"all"}}, true)
	if e != nil {
		return nil, e
	}
	out := []githubExistingIssue{}
	for _, m := range raw {
		if _, pr := m["pull_request"]; pr {
			continue
		}
		body, ok := m["body"].(string)
		if !ok || !inventorySupportMarker.MatchString(body) {
			continue
		}
		number, e := githubInventoryNumber(m)
		if e != nil {
			return nil, e
		}
		state, e := githubInventoryField(m, "state")
		if e != nil {
			return nil, e
		}
		title, e := githubInventoryField(m, "title")
		if e != nil {
			return nil, e
		}
		address, e := githubInventoryField(m, "html_url")
		if e != nil {
			return nil, e
		}
		out = append(out, githubExistingIssue{number, state, title, body, address})
	}
	return out, nil
}
func supportIssuesByFamily(items []githubExistingIssue) map[string][]githubExistingIssue {
	out := map[string][]githubExistingIssue{}
	for _, item := range items {
		for _, m := range inventorySupportMarker.FindAllStringSubmatch(item.Body, -1) {
			out[m[1]] = append(out[m[1]], item)
		}
	}
	for _, items := range out {
		sort.SliceStable(items, func(i, j int) bool { return items[i].Number < items[j].Number })
	}
	return out
}
