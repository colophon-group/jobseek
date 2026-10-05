package worker

import (
	"context"
	"encoding/json"
	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

var gupyNextData = regexp.MustCompile(`(?s)<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>`)
var gupyJobID = regexp.MustCompile(`^[1-9]\p{Nd}{0,19}$`)

func discoverGupyInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if client == nil || p.Provider != "gupy" || p.Profile != "gupy.nextdata-urls/v1" || p.Endpoint != "https://"+p.Token+".gupy.io/" {
		return result, queue.ErrConfiguration
	}
	body, response, err := fetchListingPage(ctx, client, dom.ICIMSRequest{URL: p.Endpoint}, 5_000_000)
	result.Response = response
	if err != nil {
		return result, err
	}
	return parseGupyInventory(ctx, result, p, string(body))
}

func parseGupyInventory(ctx context.Context, result RichDiscovery, p queue.GreenhouseMonitorProfile, source string) (RichDiscovery, error) {
	fail := func() (RichDiscovery, error) { return result, &DiscoveryError{Kind: "invalid_inventory"} }
	classification, err := dom.ClassifyDocument(source, dom.Object{}, p.Endpoint)
	if err != nil || classification["classification"] == "challenge" {
		return fail()
	}
	match := gupyNextData.FindStringSubmatch(source)
	if len(match) != 2 {
		return fail()
	}
	d, err := apisniffer.Decode([]byte(match[1]))
	if err != nil {
		return fail()
	}
	root, ok := d.Value.(map[string]any)
	if !ok {
		return fail()
	}
	props, ok := root["props"].(map[string]any)
	if !ok {
		return fail()
	}
	page, ok := props["pageProps"].(map[string]any)
	if !ok {
		return fail()
	}
	subdomain, _ := page["subdomain"].(string)
	if normalizeGupyInventoryTenant(subdomain) != p.Token {
		return fail()
	}
	if _, ok := page["careerPage"].(map[string]any); !ok {
		return fail()
	}
	items, ok := page["jobs"].([]any)
	if !ok {
		return fail()
	}
	urls := map[string]bool{}
	for _, raw := range items {
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		id := ""
		switch v := item["id"].(type) {
		case string:
			id = v
		case json.Number:
			id = v.String()
		}
		if gupyJobID.MatchString(id) {
			urls["https://"+p.Token+".gupy.io/jobs/"+id] = true
		}
	}
	ordered := make([]string, 0, len(urls))
	for raw := range urls {
		ordered = append(ordered, raw)
	}
	sort.Strings(ordered)
	// Invalid or duplicate IDs make the inventory incomplete, without delisting.
	result.Truncated = utf8.RuneCountInString(source) >= 5_000_000 || len(items) > 50_000 || len(ordered) != len(items)
	for _, raw := range ordered {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: raw})
	}
	return result, nil
}

func normalizeGupyInventoryTenant(value string) string {
	// The sealed profile already validates tenant syntax; matching the page's
	// lowercased/trimmed marker proves this is that tenant's own inventory.
	return strings.ToLower(strings.TrimSpace(value))
}
