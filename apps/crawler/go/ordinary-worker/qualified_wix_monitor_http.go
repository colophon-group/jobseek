package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"path"
	"strings"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"golang.org/x/net/html"
)

// The original browser exposes five Jobs records before requesting the tail.
// This complete original config has a matching public Wix collection inventory.
// Keep its browser lane and ownership binding; derive tenant/session values
// anew from its own public HTML and access-token response on every cycle.
var qualifiedWixDOMHTTPMonitors = map[string]domHTTPMonitorRoute{
	"44a767ea5bb294e90c0b3bcc8d7acc9a75bc2fd03cd182f006f7f8bfb384dca5": {"www.h3dynamics.com", "/jobs"},
}

func qualifiedWixDOMHTTPMonitor(p queue.GreenhouseMonitorProfile, config map[string]string) bool {
	return qualifiedDOMHTTPRoute(p, config, qualifiedWixDOMHTTPMonitors)
}

type wixJobsScope struct{ host, boardPath string }

func (s wixJobsScope) ResourceMatches(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Hostname() != s.host ||
		u.User != nil || u.Opaque != "" || u.Fragment != "" || (u.Port() != "" && u.Port() != "443") {
		return false
	}
	switch u.EscapedPath() {
	case s.boardPath, "/_api/v1/access-tokens":
		return u.RawQuery == ""
	case "/_api/cloud-data/v2/items/query":
		values, err := url.ParseQuery(u.RawQuery)
		return err == nil && len(values) == 1 && len(values[".r"]) == 1 && len(values[".r"][0]) <= 8192
	}
	return false
}

func (wixJobsScope) PublisherPolicyOnStatus() bool { return true }
func (wixJobsScope) HonorContentLength() bool      { return true }

func wixGridAppID(source []byte) (string, error) {
	tokens := html.NewTokenizer(strings.NewReader(string(source)))
	found := ""
	for {
		switch tokens.Next() {
		case html.ErrorToken:
			if tokens.Err() != io.EOF || found == "" {
				return "", &DiscoveryError{Kind: "wix_bootstrap"}
			}
			return found, nil
		case html.StartTagToken:
			tag := tokens.Token()
			if tag.Data != "script" {
				continue
			}
			isJSON := false
			for _, attr := range tag.Attr {
				if attr.Key == "type" && attr.Val == "application/json" {
					isJSON = true
				}
			}
			if !isJSON || tokens.Next() != html.TextToken {
				continue
			}
			var model struct {
				Features struct {
					Data struct {
						GridAppID string `json:"gridAppId"`
					} `json:"dataWixCodeSdk"`
				} `json:"siteFeaturesConfigs"`
			}
			if json.Unmarshal(tokens.Text(), &model) != nil || model.Features.Data.GridAppID == "" {
				continue
			}
			id := model.Features.Data.GridAppID
			if !wixPublicID(id) || (found != "" && found != id) {
				return "", &DiscoveryError{Kind: "wix_bootstrap"}
			}
			found = id
		}
	}
}

func wixPublicID(id string) bool {
	if len(id) != 36 {
		return false
	}
	for i, c := range id {
		if i == 8 || i == 13 || i == 18 || i == 23 {
			if c != '-' {
				return false
			}
		} else if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

func wixPublicInstance(raw []byte) (string, error) {
	var seed struct {
		Apps map[string]struct {
			IntID    int    `json:"intId"`
			Instance string `json:"instance"`
		} `json:"apps"`
	}
	if json.Unmarshal(raw, &seed) != nil {
		return "", &DiscoveryError{Kind: "wix_session"}
	}
	instance := ""
	for _, app := range seed.Apps {
		// The Wix data binding application's public internal ID. No site,
		// visitor, application-instance or token value is stored in code.
		if app.IntID != 49 {
			continue
		}
		if instance != "" || len(app.Instance) < 20 || len(app.Instance) > 16<<10 || strings.ContainsAny(app.Instance, "\r\n\x00") {
			return "", &DiscoveryError{Kind: "wix_session"}
		}
		instance = app.Instance
	}
	if instance == "" {
		return "", &DiscoveryError{Kind: "wix_session"}
	}
	return instance, nil
}

func wixJobsEndpoint(host, gridAppID string, offset int) (string, error) {
	if !wixPublicID(gridAppID) || offset < 0 || offset >= 50_000 {
		return "", queue.ErrConfiguration
	}
	query := map[string]any{
		"dataCollectionId": "Jobs", "appId": gridAppID, "environment": "LIVE",
		"referencedItemOptions": []any{}, "returnTotalCount": true,
		"query": map[string]any{
			"filter": map[string]any{}, "fields": []string{},
			"sort":   []map[string]string{{"fieldName": "_id", "order": "ASC"}},
			"paging": map[string]int{"offset": offset, "limit": 100},
		},
	}
	body, err := json.Marshal(query)
	if err != nil {
		return "", queue.ErrConfiguration
	}
	u := &url.URL{Scheme: "https", Host: host, Path: "/_api/cloud-data/v2/items/query"}
	u.RawQuery = url.Values{".r": {base64.StdEncoding.EncodeToString(body)}}.Encode()
	return u.String(), nil
}

func discoverQualifiedWixDOMHTTPMonitor(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if !qualifiedWixDOMHTTPMonitor(p, config) || client == nil {
		return result, queue.ErrConfiguration
	}
	route := qualifiedWixDOMHTTPMonitors[p.EffectiveConfigSHA256]
	scope := wixJobsScope{route.host, route.path}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(endpoint string, headers http.Header) ([]byte, error) {
		raw, response, err := fetchProviderResource(ctx, &sealed, scope, endpoint, nil, headers, 4<<20)
		if response != nil && scope.ResourceMatches(response.endpoint) && response.finalURL == endpoint {
			response.domVerification = &domVerificationBinding{p.Endpoint, p.BoardID, p.EffectiveConfigSHA256, endpoint, domVerificationConfig(config)}
		}
		result.Response = response
		// A missing token/query endpoint cannot establish that the board is gone.
		var failure *DiscoveryError
		if endpoint != p.Endpoint && errors.As(err, &failure) && failure.Kind == "provider_gone" {
			err = &DiscoveryError{Kind: "wix_api_failed", Status: failure.Status}
		}
		return raw, err
	}
	root, err := fetch(p.Endpoint, nil)
	if err != nil {
		return RichDiscovery{Response: result.Response}, err
	}
	gridAppID, err := wixGridAppID(root)
	if err != nil {
		return RichDiscovery{Response: result.Response}, err
	}
	seed, err := fetch("https://"+route.host+"/_api/v1/access-tokens", http.Header{"Accept": {"application/json"}})
	if err != nil {
		return RichDiscovery{Response: result.Response}, err
	}
	instance, err := wixPublicInstance(seed)
	if err != nil {
		return RichDiscovery{Response: result.Response}, err
	}
	headers := http.Header{"Accept": {"application/json"}, "Authorization": {instance}}
	expected := -1
	seenIDs, seenURLs := map[string]bool{}, map[string]bool{}
	for offset := 0; offset < 50_000; offset += 100 {
		endpoint, err := wixJobsEndpoint(route.host, gridAppID, offset)
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		raw, err := fetch(endpoint, headers)
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		var page struct {
			Items []struct {
				ID   string `json:"id"`
				Data struct {
					Link string `json:"link-jobs-title"`
				} `json:"data"`
			} `json:"dataItems"`
			Paging *struct {
				Count  *int `json:"count"`
				Offset *int `json:"offset"`
				Total  *int `json:"total"`
			} `json:"pagingMetadata"`
		}
		if json.Unmarshal(raw, &page) != nil || page.Paging == nil || page.Items == nil ||
			page.Paging.Offset == nil || page.Paging.Count == nil || page.Paging.Total == nil || *page.Paging.Offset != offset ||
			*page.Paging.Count != len(page.Items) || len(page.Items) > 100 || *page.Paging.Total < 0 || *page.Paging.Total > 50_000 ||
			(expected >= 0 && *page.Paging.Total != expected) || len(page.Items) != min(100, *page.Paging.Total-offset) {
			return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "wix_inventory"}
		}
		expected = *page.Paging.Total
		for _, item := range page.Items {
			u, err := url.Parse(item.Data.Link)
			if err != nil || u.User != nil || u.Opaque != "" || u.RawQuery != "" || u.Fragment != "" {
				return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "wix_inventory"}
			}
			base := &url.URL{Scheme: "https", Host: route.host, Path: route.path}
			u = base.ResolveReference(u)
			if !wixPublicID(item.ID) || seenIDs[item.ID] || seenURLs[u.String()] || u.Scheme != "https" ||
				u.Host != route.host || !strings.HasPrefix(u.EscapedPath(), "/jobs/") || path.Clean(u.Path) != strings.TrimSuffix(u.Path, "/") {
				return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "wix_inventory"}
			}
			seenIDs[item.ID], seenURLs[u.String()] = true, true
			result.Jobs = append(result.Jobs, RichMonitorJob{URL: u.String(), URLOnly: true})
		}
		if len(result.Jobs) == expected {
			if expected == 0 {
				result.VerifiedEmptyReason = "Source-bound Wix Jobs collection reports total zero"
			}
			return result, nil
		}
	}
	return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "wix_inventory"}
}
