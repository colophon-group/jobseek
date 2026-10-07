package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	sitemap "github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/sitemap"
)

var errPCSXDisabled = errors.New("PCSX disabled")
var errPCSXStableBlock = errors.New("PCSX stable block")

func fetchPCSXPage(ctx context.Context, client *http.Client, o api.EightfoldOptions, domain string, offset, num int, wait func(context.Context, time.Duration) error) ([]map[string]any, *GreenhouseResponse, error) {
	endpoint := o.SearchURL(domain, offset, num)
	var observed *GreenhouseResponse
	for attempt := 0; attempt < 3; attempt++ {
		requestCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
		raw, response, e := fetchProviderStatusResource(requestCtx, client, o, endpoint, nil, nil, 10<<20, map[int]bool{403: true})
		cancel()
		observed = response
		if ctx.Err() != nil {
			return nil, observed, ctx.Err()
		}
		var reserved *policy.Reservation
		if errors.As(e, &reserved) {
			return nil, observed, e
		}
		status := 0
		if observed != nil {
			status = observed.status
		}
		if status == 403 {
			d, de := api.Decode(raw)
			if de == nil {
				if value, ok := d.Value.(map[string]any); ok {
					if message, ok := value["message"].(string); ok {
						message = strings.ToLower(message)
						if strings.Contains(message, "pcsx") && strings.Contains(message, "not enabled") {
							return nil, observed, errPCSXDisabled
						}
					}
				}
			}
			return nil, observed, e
		}
		if status == 405 {
			return nil, observed, errPCSXStableBlock
		}
		delay := time.Duration(attempt+1) * time.Second
		if e == nil {
			d, de := api.Decode(raw)
			if de != nil {
				delay = time.Duration(attempt+1) * time.Second / 2
			} else {
				value, ok := d.Value.(map[string]any)
				if !ok {
					return nil, observed, api.ErrInventory
				}
				inner, ok := value["data"].(map[string]any)
				if value["data"] != nil && !ok {
					return nil, observed, api.ErrInventory
				}
				positions := inner["positions"]
				if positions == nil || positions == false || positions == "" {
					return []map[string]any{}, observed, nil
				}
				rows, ok := positions.([]any)
				if !ok {
					return nil, observed, api.ErrInventory
				}
				out := make([]map[string]any, 0, len(rows))
				for _, item := range rows {
					row, ok := item.(map[string]any)
					if !ok {
						return nil, observed, api.ErrInventory
					}
					out = append(out, row)
				}
				return out, observed, nil
			}
		} else if status != 0 {
			if status != 408 && status != 425 && status != 429 && (status < 500 || status > 599) {
				return nil, observed, e
			}
			delay = time.Duration(float64(5*time.Second) * float64(int64(1)<<attempt) * (0.8 + rand.Float64()*0.4))
		}
		if e := wait(ctx, delay); e != nil {
			return nil, observed, e
		}
	}
	return nil, observed, &DiscoveryError{Kind: "pcsx_fetch_failed"}
}

func discoverEightfoldInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverEightfoldInventoryAt(ctx, client, p, config, time.Now().UTC().Truncate(time.Microsecond), pauseRich)
}

func discoverEightfoldInventoryAt(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, now time.Time, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := queue.EightfoldMonitorOptions(config)
	if e != nil || client == nil || wait == nil || p.Provider != "eightfold" || (p.Profile != "eightfold.pcsx-sitemap/v1" && p.Profile != "eightfold.proxy-pcsx-sitemap/v1") || p.Endpoint != o.SitemapURL {
		return result, queue.ErrConfiguration
	}
	c, e := sitemap.NormalizeConfig(sitemap.Config{SitemapURL: o.SitemapURL, MaxURLs: 50000, MaxIndexChildren: 200, MaxIndexDepth: 8, ChildMaxAttempts: 3})
	if e != nil {
		return result, e
	}
	operation := *client
	operation.Jar = nil
	operation.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	session := &nativeSitemapSession{client: &operation, endpoint: o.SitemapURL}
	found, e := sitemap.RunWithSession(ctx, c, session)
	result.Response, result.Truncated = session.response, found.Truncated
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	if e != nil {
		return result, &DiscoveryError{Kind: "inventory_failed", cause: e}
	}
	urls := []string{}
	for _, source := range found.URLs {
		if !strings.Contains(source, "/careers/job/") {
			continue
		}
		u, e := url.Parse(source)
		if e != nil || "https://"+u.Host != o.Origin || u.User != nil {
			return RichDiscovery{Response: result.Response}, api.ErrInventory
		}
		urls = append(urls, source)
	}
	sort.Strings(urls)
	for _, source := range urls {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: source, Hybrid: true, URLOnly: true})
	}
	if len(urls) == 0 {
		return result, nil
	}
	domain := ""
	for _, source := range urls {
		value := api.EightfoldDomain(source, true)
		if value == "" {
			continue
		}
		if domain != "" && domain != value {
			return RichDiscovery{Response: result.Response}, api.ErrInventory
		}
		domain = value
	}
	u, _ := url.Parse(o.Origin)
	if domain == "" {
		domain = u.Hostname()
	}
	w, e := api.EightfoldReadWatermark(o.Metadata)
	if e != nil {
		return RichDiscovery{Response: result.Response}, e
	}
	setState := func(enabled bool) { w.Enabled = &enabled; w.Extra["host"] = u.Hostname(); w.Extra["domain"] = domain }
	finishDisabled := func() (RichDiscovery, error) {
		setState(false)
		if !result.Truncated {
			result.MetadataUpdates = w.Patch()
		}
		return result, nil
	}
	if w.Enabled == nil || w.NeedsFull(now) || o.ForceFull() {
		_, observed, e := fetchPCSXPage(ctx, &operation, o, domain, 0, 1, wait)
		if observed != nil {
			result.Response = observed
		}
		var reserved *policy.Reservation
		if ctx.Err() != nil {
			return RichDiscovery{Response: result.Response}, ctx.Err()
		}
		if errors.As(e, &reserved) {
			return RichDiscovery{Response: result.Response}, e
		}
		if errors.Is(e, errPCSXDisabled) {
			return finishDisabled()
		}
		if e != nil {
			if w.Enabled != nil && !*w.Enabled {
				return finishDisabled()
			}
			return result, nil
		}
		enabled := true
		w.Enabled = &enabled
	}
	if w.Enabled != nil && !*w.Enabled {
		return finishDisabled()
	}
	full := o.ForceFull() || w.NeedsFull(now)
	if full && w.MaxTS == 0 && !w.AutoFullCrawl && !o.ForceFull() {
		setState(true)
		if !result.Truncated {
			result.MetadataUpdates = w.Patch()
		}
		return result, nil
	}
	cap := 500
	if full {
		cap = 5000
	}
	rows := []map[string]any{}
	safety := -1
	finished := false
	for page := 0; page < cap; page++ {
		items, observed, e := fetchPCSXPage(ctx, &operation, o, domain, page*10, 10, wait)
		if observed != nil {
			result.Response = observed
		}
		var reserved *policy.Reservation
		if ctx.Err() != nil {
			return RichDiscovery{Response: result.Response}, ctx.Err()
		}
		if errors.As(e, &reserved) {
			return RichDiscovery{Response: result.Response}, e
		}
		if errors.Is(e, errPCSXDisabled) || errors.Is(e, errPCSXStableBlock) {
			return finishDisabled()
		}
		if e != nil {
			return result, nil
		} // authoritative sitemap remains; watermark never advances on a failed prefix
		if len(items) == 0 {
			finished = true
			break
		}
		if e := wait(ctx, 200*time.Millisecond); e != nil {
			return RichDiscovery{Response: result.Response}, e
		}
		rows = append(rows, items...)
		if len(rows) > 50000 {
			return RichDiscovery{Response: result.Response}, api.ErrInventory
		}
		if !full {
			allOld := true
			for _, item := range items {
				ts, ok := api.EightfoldInt(item["postedTs"])
				if !ok || ts <= 0 || ts > w.MaxTS {
					allOld = false
					break
				}
			}
			if allOld {
				if safety < 0 {
					safety = 3
				}
				if safety == 0 {
					finished = true
					break
				}
				safety--
			} else if safety >= 0 {
				safety = -1
			}
		}
	}
	if !finished {
		return RichDiscovery{Response: result.Response}, &DiscoveryError{Kind: "pcsx_page_limit"}
	}
	idToURL := map[string]string{}
	for _, source := range urls {
		id := api.EightfoldJobID(source)
		if id != "" && idToURL[id] == "" {
			idToURL[id] = source
		}
	}
	jobs := map[string]RichMonitorJob{}
	for _, raw := range rows {
		position, _ := raw["positionUrl"].(string)
		source := idToURL[api.EightfoldJobID(position)]
		if source == "" {
			continue
		}
		values := api.EightfoldPCSXFields(raw, source)
		title, e := executor.CoerceText(values["title"])
		if e != nil {
			return RichDiscovery{Response: result.Response}, e
		}
		locations, e := executor.CoerceLocations(values["locations"])
		if e != nil {
			return RichDiscovery{Response: result.Response}, e
		}
		metadata, _ := values["metadata"].(map[string]any)
		jobs[source] = RichMonitorJob{URL: source, Title: title, Locations: locations, DatePosted: values["date_posted"], JobLocationType: values["job_location_type"], Metadata: metadata, Hybrid: true}
		if ts, ok := api.EightfoldInt(raw["postedTs"]); ok && ts > w.MaxTS {
			w.MaxTS = ts
		}
	}
	for i, job := range result.Jobs {
		if rich, ok := jobs[job.URL]; ok {
			result.Jobs[i] = rich
		}
	}
	setState(true)
	if full {
		w.LastFullAt = &now
	}
	w.LastIncrementalAt = &now
	if !result.Truncated {
		result.MetadataUpdates = w.Patch()
	}
	return result, nil
}
