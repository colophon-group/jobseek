package worker

import (
	"bytes"
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func localizedHTTPFetcher(client *http.Client, provider, board string, scope providerResourceScope, wait func(context.Context, time.Duration) error, observed *lastHTTPObservation, application func(string) bool) (api.SuccessFactorsLegacyFetch, error) {
	if client == nil || scope == nil || wait == nil || observed == nil {
		return nil, queue.ErrConfiguration
	}
	sealed := *client
	sealed.Jar = nil
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	jar, e := cookiejar.New(nil)
	if e != nil {
		return nil, e
	}
	return func(ctx context.Context, r api.Request) ([]byte, http.Header, error) {
		if !scope.ResourceMatches(r.URL) || r.Method != "GET" && r.Method != "POST" || r.Method == "POST" && (provider != "prospective" || r.URL != board) {
			return nil, nil, queue.ErrConfiguration
		}
		isApplication := application != nil && application(r.URL)
		u, _ := url.Parse(r.URL)
		isPDF := provider == "kipt" && strings.HasSuffix(strings.ToLower(u.Path), ".pdf")
		attempts := 3
		if isApplication || isPDF {
			attempts = 1
		}
		limit := int64(64 << 20)
		if provider == "prospective" {
			limit = 2 << 20
		}
		if isPDF {
			limit = 40 << 20
		}
		for attempt := 0; attempt < attempts; attempt++ {
			current := r
			current.Headers = r.Headers.Clone()
			visited := map[string]bool{}
			var body []byte
			var headers http.Header
			var response *GreenhouseResponse
			var err error
			for hop := 0; hop <= 20; hop++ {
				if !scope.ResourceMatches(current.URL) || visited[current.URL] {
					return nil, nil, queue.ErrConfiguration
				}
				visited[current.URL] = true
				target, _ := url.Parse(current.URL)
				current.Headers.Del("Cookie")
				for _, cookie := range jar.Cookies(target) {
					if current.Headers.Get("Cookie") == "" {
						current.Headers.Set("Cookie", cookie.String())
					} else {
						current.Headers.Set("Cookie", current.Headers.Get("Cookie")+"; "+cookie.String())
					}
				}
				body, headers, response, err = fetchLastHTTPOnce(ctx, &sealed, scope, current, limit)
				observed.observe(response)
				if ctx.Err() != nil {
					return nil, headers, ctx.Err()
				}
				var reserved *policy.Reservation
				if errors.As(err, &reserved) {
					return nil, headers, err
				}
				jar.SetCookies(target, api.OriginalSessionCookies(headers))
				status := 0
				if response != nil {
					status = response.status
				}
				if isApplication {
					if headers == nil {
						headers = http.Header{}
					}
					headers.Set("X-Jobseek-Observed-Status", strconv.Itoa(status))
					if status >= 200 && status < 300 || status >= 300 && status < 400 {
						return body, headers, nil
					}
					break
				}
				if status == 301 || status == 302 || status == 303 || status == 307 || status == 308 {
					location := headers.Get("Location")
					if location == "" || hop == 20 {
						return nil, headers, queue.ErrConfiguration
					}
					rel, e := url.Parse(location)
					if e != nil {
						return nil, headers, queue.ErrConfiguration
					}
					next := target.ResolveReference(rel).String()
					if !scope.ResourceMatches(next) {
						return nil, headers, queue.ErrConfiguration
					}
					current.URL = next
					if status == 303 || current.Method == "POST" && (status == 301 || status == 302) {
						current.Method = "GET"
						current.Body = ""
						current.Headers.Del("Content-Type")
					}
					continue
				}
				break
			}
			status := 0
			if response != nil {
				status = response.status
			}
			if err == nil && status == 200 && (isPDF || strings.TrimSpace(string(body)) != "") {
				if !isPDF {
					body = []byte(jsonld.DecodeDocument(body, response.contentType))
				}
				return body, headers, nil
			}
			var failed *DiscoveryError
			terminal := errors.As(err, &failed) && failed.Kind == "body_limit"
			retry := !terminal && (status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599 || (provider == "prospective" || provider == "talemetry" && !strings.HasSuffix(u.Path, ".json")) && status == 403)
			if !retry || attempt+1 == attempts {
				return nil, headers, &remainingHTTPStatusError{status: status, cause: err}
			}
			if e := wait(ctx, time.Duration(float64(500*time.Millisecond)*float64(uint64(1)<<uint(attempt))*(0.5+rand.Float64()))); e != nil {
				return nil, headers, e
			}
		}
		return nil, nil, api.ErrInventory
	}, nil
}

func extractKIPTPDFText(ctx context.Context, body []byte) (string, error) {
	if len(body) == 0 || len(body) > 40<<20 || !bytes.HasPrefix(bytes.TrimSpace(body), []byte("%PDF")) {
		return "", errPDFBinary
	}
	ctx, cancel := context.WithTimeout(ctx, 180*time.Second)
	defer cancel()
	dir, e := os.MkdirTemp("", "jobseek-kipt-pdf-")
	if e != nil {
		return "", errPDFBinary
	}
	defer os.RemoveAll(dir)
	file := filepath.Join(dir, "source.pdf")
	if os.WriteFile(file, body, 0o600) != nil {
		return "", errPDFBinary
	}
	text, e := pdfCommand(ctx, 16<<20, "pdftotext", "-layout", "-enc", "UTF-8", file, "-")
	if e != nil {
		return "", e
	}
	// Preserve the original blank separation between document pages; parsing
	// and stable identity use the full vacancy line, not a document URL alone.
	pages := strings.Split(string(text), "\f")
	for i, p := range pages {
		pages[i] = strings.TrimSpace(p)
	}
	if len(pages) > 0 && pages[len(pages)-1] == "" {
		pages = pages[:len(pages)-1]
	}
	return strings.TrimSpace(strings.Join(pages, "\n\n")), nil
}

func FetchLocalizedHTTPProviders(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	if config["crawler_type"] != p.Provider || config["monitor_needs_browser"] != "0" || !queue.SecondaryMonitorResourceMatches(p, config, p.Endpoint) {
		return out, queue.ErrConfiguration
	}
	observed := &lastHTTPObservation{}
	var scope providerResourceScope
	var application func(string) bool
	var talemetry api.TalemetryOptions
	var prospective api.ProspectiveOptions
	var kipt api.KIPTOptions
	var e error
	switch p.Provider {
	case "talemetry":
		talemetry, e = api.TalemetryOptionsFromMetadata(config["board_url"], config["metadata"])
		scope = talemetry
	case "prospective":
		prospective, e = api.ProspectiveOptionsFromMetadata(config["board_url"], config["metadata"])
		scope = prospective
		application = prospective.ApplicationResource
	case "kipt":
		kipt, e = api.KIPTOptionsFromMetadata(config["board_url"], config["metadata"])
		scope = kipt
	default:
		return out, queue.ErrConfiguration
	}
	if e != nil {
		return out, e
	}
	fetch, e := localizedHTTPFetcher(client, p.Provider, config["board_url"], scope, wait, observed, application)
	if e != nil {
		return out, e
	}
	plain := func(ctx context.Context, r api.Request) ([]byte, error) { body, _, e := fetch(ctx, r); return body, e }
	fields := []map[string]any{}
	switch p.Provider {
	case "talemetry":
		var urls []string
		urls, e = api.DiscoverTalemetry(ctx, talemetry, plain, wait)
		for _, u := range urls {
			out.Jobs = append(out.Jobs, RichMonitorJob{URL: u, URLOnly: true})
		}
	case "prospective":
		fields, e = api.DiscoverProspective(ctx, prospective, fetch, func(raw string, body []byte) (map[string]any, error) {
			return jsonld.Parse(raw, body, map[string]any{})
		})
	case "kipt":
		listing := kipt.BoardURL
		var body []byte
		body, _, e = fetch(ctx, api.Request{Method: "GET", URL: listing})
		var failed *remainingHTTPStatusError
		if errors.As(e, &failed) && (failed.status == 404 || failed.status == 410) {
			listing = kipt.AlternateURL()
			body, _, e = fetch(ctx, api.Request{Method: "GET", URL: listing})
			if errors.As(e, &failed) && (failed.status == 404 || failed.status == 410) {
				e = &DiscoveryError{Kind: "provider_gone", Status: failed.status}
			}
		}
		if e == nil {
			var jobs []api.Job
			jobs, e = api.DiscoverKIPT(ctx, kipt, time.Now().UTC().Format("2006-01-02"), listing, body, func(ctx context.Context, raw string) (string, error) {
				b, _, e := fetch(ctx, api.Request{Method: "GET", URL: raw})
				if e != nil {
					return "", e
				}
				return extractKIPTPDFText(ctx, b)
			})
			for _, j := range jobs {
				fields = append(fields, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "date_posted": j.DatePosted, "metadata": j.Metadata, "language": j.Extras["language"]})
			}
		}
	}
	out.Response = observed.latest()
	if e != nil {
		return RichDiscovery{Response: out.Response}, e
	}
	for _, f := range fields {
		job, e := secondaryRichJob(f)
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		localizations, _ := f["localizations"].(map[string]any)
		locales := []string{}
		for locale := range localizations {
			locales = append(locales, locale)
		}
		sort.Strings(locales)
		for _, locale := range locales {
			job.LocalizationLocales = append(job.LocalizationLocales, locale)
			localized, _ := localizations[locale].(map[string]any)
			if title, ok := localized["title"].(string); ok {
				job.LocalizedTitles = append(job.LocalizedTitles, title)
			}
		}
		out.Jobs = append(out.Jobs, job)
	}
	out.Truncated = len(out.Jobs) > 50_000
	if out.Truncated {
		out.Jobs = out.Jobs[:50_000]
	}
	return out, nil
}
