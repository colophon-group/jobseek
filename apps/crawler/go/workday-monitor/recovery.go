package workday

import (
	"context"
	"encoding/json"
	"errors"
	"math/rand"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strings"
)

var ErrRecoveryConfig = errors.New("invalid configured Workday recovery")
var recoveryCompany = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9-]{0,127}$`)
var recoveryJob = regexp.MustCompile(`/job/([A-Za-z0-9._-]{1,128})(?:/|$)`)

type RecoveryConfig struct {
	Company, Instance, Site string
	Proxy                   bool
}

func ParseRecoveryConfig(value any) (*RecoveryConfig, error) {
	if value == nil {
		return nil, nil
	}
	m, ok := value.(map[string]any)
	if !ok || len(m) != 4 {
		return nil, ErrRecoveryConfig
	}
	company, cok := m["company"].(string)
	instance, iok := m["wd_instance"].(string)
	site, sok := m["site"].(string)
	proxy, pok := m["proxy"].(bool)
	if !cok || !iok || !sok || !pok || !recoveryCompany.MatchString(company) || !instanceToken.MatchString(instance) || !siteToken.MatchString(site) {
		return nil, ErrRecoveryConfig
	}
	return &RecoveryConfig{company, instance, site, proxy}, nil
}

type RecoveryResult struct {
	Found               bool
	URL                 string
	Detail              DetailFetchResult
	Requests, Responses int
	Bytes               int64
}

// RecoverPhenomDetailWithClient searches one configured tenant for one exact
// requisition identity. Missing or ambiguous matches never select a description.
// The caller owns transport credentials and the canonical Phenom posting URL.
func RecoverPhenomDetailWithClient(ctx context.Context, source string, config RecoveryConfig, client *http.Client) (RecoveryResult, error) {
	result := RecoveryResult{}
	if client == nil || !recoveryCompany.MatchString(config.Company) || !instanceToken.MatchString(config.Instance) || !siteToken.MatchString(config.Site) {
		return result, ErrRecoveryConfig
	}
	parsed, err := url.Parse(source)
	if err != nil {
		return result, nil
	}
	match := recoveryJob.FindStringSubmatch(parsed.EscapedPath())
	if match == nil {
		return result, nil
	}
	id := match[1]
	site := Site{Company: config.Company, Instance: config.Instance, Name: config.Site}
	copy := *client
	copy.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	poster := &LivePoster{client: &copy, tenant: &site, allowedSites: map[string]bool{site.Name: true}, sleep: sleepContext, random: rand.Float64}
	payload, _ := json.Marshal(map[string]any{"appliedFacets": map[string]any{}, "limit": 20, "offset": 0, "searchText": id})
	endpoint := "https://" + site.Company + "." + site.Instance + ".myworkdayjobs.com/wday/cxs/" + site.Company + "/" + site.Name + "/jobs"
	body, err := poster.Post(ctx, endpoint, payload)
	result.Requests, result.Responses, result.Bytes = poster.Requests, poster.Responses, poster.Bytes
	if err != nil {
		return result, err
	}
	var page map[string]json.RawMessage
	if json.Unmarshal(body, &page) != nil {
		return result, errors.New("invalid Workday recovery search response")
	}
	var rows []json.RawMessage
	if json.Unmarshal(page["jobPostings"], &rows) != nil {
		return result, nil
	}
	paths := map[string]bool{}
	for _, raw := range rows {
		var row map[string]json.RawMessage
		if json.Unmarshal(raw, &row) != nil {
			continue
		}
		var path string
		if json.Unmarshal(row["externalPath"], &path) != nil || !strings.HasPrefix(path, "/job/") || crossSitePathKey(path) != "requisition:"+id {
			continue
		}
		paths[path] = true
	}
	selected := make([]string, 0, len(paths))
	for path := range paths {
		selected = append(selected, path)
	}
	sort.Strings(selected)
	if len(selected) != 1 {
		return result, nil
	}
	result.URL = "https://" + site.Company + "." + site.Instance + ".myworkdayjobs.com/" + site.Name + selected[0]
	result.Detail, err = FetchDetailWithClient(ctx, result.URL, nil, client)
	result.Requests += result.Detail.Requests
	result.Responses += result.Detail.Responses
	result.Bytes += result.Detail.Bytes
	if err != nil {
		return result, err
	}
	result.Found = result.Detail.Content.Title != nil && *result.Detail.Content.Title != ""
	return result, nil
}
