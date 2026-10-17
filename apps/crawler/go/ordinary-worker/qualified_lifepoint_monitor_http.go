package worker

import (
	"context"
	"crypto/sha256"
	"errors"
	"net/http"
	"net/url"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// The original configured browser capture skips even-numbered result pages.
// The public endpoint supplies every page, including all original jobs. Keep
// the original full config and lane; never infer an empty page from a failure.
var qualifiedLifepointDOMHTTPMonitors = map[string]domHTTPMonitorRoute{
	"cb7508c3796122b4ce9fa4bcdf5f28fffdb2743248f49fcb9434419e2d5bf9d0": {"providercareers.lifepointhealth.net", "/jobs/"},
}

func qualifiedLifepointDOMHTTPMonitor(p queue.GreenhouseMonitorProfile, config map[string]string) bool {
	return qualifiedDOMHTTPRoute(p, config, qualifiedLifepointDOMHTTPMonitors)
}

var lifepointPublicCalcToken = regexp.MustCompile(`"calcToken"\s*:\s*"([0-9]{1,10}\.[0-9a-f]{32})"`)
var lifepointCalcTokenValue = regexp.MustCompile(`^[0-9]{1,10}\.[0-9a-f]{32}$`)

type lifepointJobsScope struct{ boardHost, boardPath, resultHost string }

func (s lifepointJobsScope) ResourceMatches(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Fragment != "" ||
		(u.Port() != "" && u.Port() != "443") {
		return false
	}
	if u.Hostname() == s.boardHost && u.EscapedPath() == s.boardPath && u.RawQuery == "" {
		return true
	}
	if u.Hostname() == s.resultHost && u.EscapedPath() == s.boardPath {
		values, err := url.ParseQuery(u.RawQuery)
		return err == nil && len(values) == 1 && slices.Equal(values["filter[category][]"], []string{"Physician", "Advanced Practice Provider"})
	}
	if u.Hostname() != s.resultHost || u.EscapedPath() != "/calc-results/" {
		return false
	}
	values, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(values) != 3 || len(values["mypage"]) != 1 || len(values["ctok"]) != 1 ||
		!slices.Equal(values["filter[category][]"], []string{"Physician", "Advanced Practice Provider"}) ||
		!lifepointCalcTokenValue.MatchString(values["ctok"][0]) {
		return false
	}
	page, err := strconv.Atoi(values["mypage"][0])
	return err == nil && page >= 0 && page <= 2000 && strconv.Itoa(page) == values["mypage"][0]
}

func (lifepointJobsScope) PublisherPolicyOnStatus() bool { return true }
func (lifepointJobsScope) HonorContentLength() bool      { return true }

func lifepointPageURL(host, token string, page int) string {
	u := &url.URL{Scheme: "https", Host: host, Path: "/calc-results/"}
	u.RawQuery = url.Values{
		"filter[category][]": {"Physician", "Advanced Practice Provider"},
		"mypage":             {strconv.Itoa(page)}, "ctok": {token},
	}.Encode()
	return u.String()
}

func discoverQualifiedLifepointDOMHTTPMonitor(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverQualifiedLifepointDOMHTTPMonitorWithWait(ctx, client, p, config, pauseRich)
}

func discoverQualifiedLifepointDOMHTTPMonitorWithWait(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if !qualifiedLifepointDOMHTTPMonitor(p, config) || client == nil || wait == nil {
		return result, queue.ErrConfiguration
	}
	listing, _, err := queue.RenderedDOMMonitorOptions(config)
	rules, ruleErr := queue.FeedMonitorURLRules(config)
	if err != nil || ruleErr != nil || rules.HasTransform() || listing.Pagination != nil || listing.FetchURL != "" ||
		listing.RequireJSONLD || listing.RichRows != nil || listing.ScriptLinks != nil || listing.PDFProof != nil || listing.HasDetailFilters() {
		return result, queue.ErrConfiguration
	}
	route := qualifiedLifepointDOMHTTPMonitors[p.EffectiveConfigSHA256]
	scope := lifepointJobsScope{route.host, route.path, "jobs.lifepointhealth.net"}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(endpoint string, headers http.Header) ([]byte, error) {
		raw, response, err := fetchProviderResource(ctx, &sealed, scope, endpoint, nil, headers, 4<<20)
		if response != nil && scope.ResourceMatches(response.endpoint) && response.finalURL == endpoint {
			response.domVerification = &domVerificationBinding{p.Endpoint, p.BoardID, p.EffectiveConfigSHA256, endpoint, domVerificationConfig(config)}
		}
		result.Response = response
		return raw, err
	}
	var root []byte
	rootEndpoint := p.Endpoint
	for hop := 0; ; hop++ {
		root, err = fetch(rootEndpoint, nil)
		if err == nil {
			break
		}
		var status *DiscoveryError
		observed := result.Response
		if hop != 0 || observed == nil || !errors.As(err, &status) || status.Kind != "http_status" ||
			(observed.status != 301 && observed.status != 302 && observed.status != 307 && observed.status != 308) {
			return RichDiscovery{Response: observed}, err
		}
		base, parseErr := url.Parse(rootEndpoint)
		target, targetErr := url.Parse(observed.location)
		if parseErr != nil || targetErr != nil || observed.location == "" {
			return RichDiscovery{Response: observed}, &DiscoveryError{Kind: "lifepoint_redirect"}
		}
		target = base.ResolveReference(target)
		if target.Hostname() != scope.resultHost || target.EscapedPath() != scope.boardPath || !scope.ResourceMatches(target.String()) {
			return RichDiscovery{Response: observed}, &DiscoveryError{Kind: "lifepoint_redirect"}
		}
		rootEndpoint = target.String()
	}
	matches := lifepointPublicCalcToken.FindAllSubmatch(root, -1)
	if len(matches) == 0 {
		return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "lifepoint_bootstrap"}
	}
	token := string(matches[0][1])
	for _, match := range matches[1:] {
		if string(match[1]) != token {
			return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "lifepoint_bootstrap"}
		}
	}
	seenURLs, seenPages := map[string]bool{}, map[[32]byte]bool{}
	for page := 0; page <= 2000; page++ {
		if err := wait(ctx, time.Second); err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		endpoint := lifepointPageURL(scope.resultHost, token, page)
		raw, err := fetch(endpoint, http.Header{"Accept": {"*/*"}, "X-Requested-With": {"XMLHttpRequest"}})
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		// This source's successful terminal result with its fresh public nonce
		// is an exactly empty body. Nonempty HTML with no jobs is not that signal.
		if len(raw) == 0 {
			if page == 0 {
				result.VerifiedEmptyReason = "Source-bound provider query returned an empty first result page"
			}
			return result, nil
		}
		digest := sha256.Sum256(raw)
		if seenPages[digest] {
			return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "lifepoint_repeated_page"}
		}
		seenPages[digest] = true
		// Retain browser href resolution against the independently witnessed
		// response origin. The official board identity and ownership stay intact.
		parsed, err := parseDOMInventory(ctx, RichDiscovery{Response: result.Response}, p, listing, string(raw), result.Response.finalURL, true)
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		jobs, err := applyFeedMonitorURLs(ctx, config, parsed.Jobs)
		if err != nil || len(jobs) == 0 || parsed.Truncated {
			return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "lifepoint_inventory"}
		}
		added := 0
		for _, job := range jobs {
			u, err := url.Parse(job.URL)
			if err != nil || u.Scheme != "https" || u.User != nil || u.Hostname() != scope.resultHost ||
				(u.Port() != "" && u.Port() != "443") || !strings.HasPrefix(u.EscapedPath(), "/job/") || u.RawQuery != "" || u.Fragment != "" {
				return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "lifepoint_inventory"}
			}
			if !seenURLs[job.URL] {
				seenURLs[job.URL] = true
				result.Jobs = append(result.Jobs, job)
				added++
			}
		}
		if added == 0 || len(result.Jobs) > 50_000 {
			return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "lifepoint_inventory"}
		}
	}
	return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "lifepoint_page_limit"}
}
