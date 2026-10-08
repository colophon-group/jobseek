package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"regexp"
	"sort"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

type NotionFetch func(context.Context, Request) (*Document, int, error)

func NotionAPIRequest(o NotionOptions, endpoint string, payload map[string]any, canonical bool) (Request, error) {
	host := o.Subdomain + ".notion.site"
	if canonical {
		if endpoint != "getPublicPageData" || notionUUID.MatchString(o.Hint) {
			return Request{}, ErrOptions
		}
		host = "www.notion.so"
	}
	resource := "https://" + host + "/api/v3/" + endpoint
	if !o.ResourceMatches(resource) {
		return Request{}, ErrOptions
	}
	body, err := json.Marshal(payload)
	if err != nil || len(body) > 1<<20 {
		return Request{}, ErrOptions
	}
	return Request{Method: "POST", URL: resource, Body: string(body), Headers: http.Header{"Content-Type": {"application/json"}}}, nil
}

func NotionChunkRequest(o NotionOptions, id string, cursor map[string]any, chunk int) (Request, error) {
	if !notionUUID.MatchString(id) || chunk < 0 || chunk >= 64 {
		return Request{}, ErrOptions
	}
	if cursor == nil {
		cursor = map[string]any{"stack": []any{}}
	}
	return NotionAPIRequest(o, "loadPageChunk", map[string]any{"page": map[string]any{"id": id}, "limit": 200, "cursor": cursor, "chunkNumber": chunk, "verticalColumns": false}, false)
}

func mergeNotionChunks(target, source *Document) error {
	root := asEmbeddedObject(target.Value)
	from := asEmbeddedObject(source.Value)
	recordMap := asEmbeddedObject(root["recordMap"])
	next := asEmbeddedObject(from["recordMap"])
	if len(recordMap) == 0 || len(next) == 0 {
		return ErrInventory
	}
	for pointer, order := range source.order {
		target.order[pointer] = order
	}
	for _, kind := range source.ObjectKeys(next) {
		incoming := asEmbeddedObject(next[kind])
		current := asEmbeddedObject(recordMap[kind])
		if current == nil {
			recordMap[kind] = incoming
			target.order[reflect.ValueOf(recordMap)] = append(target.order[reflect.ValueOf(recordMap)], kind)
			continue
		}
		for _, key := range source.ObjectKeys(incoming) {
			if _, exists := current[key]; !exists {
				target.order[reflect.ValueOf(current)] = append(target.order[reflect.ValueOf(current)], key)
			}
			current[key] = incoming[key]
		}
		if len(current) > 10000 {
			return ErrInventory
		}
	}
	return nil
}

func LoadNotionChunk(ctx context.Context, o NotionOptions, id string, fetch NotionFetch) (*Document, error) {
	var merged *Document
	var cursor map[string]any
	seen := map[string]bool{}
	for chunk := 0; chunk < 64; chunk++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		request, err := NotionChunkRequest(o, id, cursor, chunk)
		if err != nil {
			return nil, err
		}
		d, _, err := fetch(ctx, request)
		if err != nil {
			return nil, err
		}
		if d == nil {
			return nil, ErrInventory
		}
		if len(notionRecords(d, "block")) > 10000 {
			return nil, ErrInventory
		}
		if merged == nil {
			merged = d
		} else if err := mergeNotionChunks(merged, d); err != nil {
			return nil, err
		}
		cursor = asEmbeddedObject(asEmbeddedObject(d.Value)["cursor"])
		stack, ok := cursor["stack"].([]any)
		if !ok && cursor["stack"] != nil {
			return nil, ErrInventory
		}
		if len(stack) == 0 {
			return merged, nil
		}
		body, err := json.Marshal(cursor)
		if err != nil || len(body) > 1<<20 || seen[string(body)] {
			return nil, ErrInventory
		}
		seen[string(body)] = true
	}
	return nil, ErrInventory
}

var notionSlugNonASCII = regexp.MustCompile(`[^a-z0-9]+`)

func notionFindSlug(d *Document, hint string) string {
	hint = strings.ReplaceAll(strings.ToLower(hint), " ", "-")
	blocks := notionRecords(d, "block")
	for _, id := range d.ObjectKeys(blocks) {
		record := notionRecord(blocks[id])
		if record["type"] != "page" && record["type"] != "collection_view_page" {
			continue
		}
		title := strings.Trim(notionSlugNonASCII.ReplaceAllString(strings.ToLower(notionTitle(record)), "-"), "-")
		if title == hint {
			return id
		}
	}
	return ""
}

func DiscoverNotion(ctx context.Context, o NotionOptions, fetch NotionFetch) ([]string, error) {
	payload := map[string]any{"type": "block-space", "name": "page", "requestedOnPublicDomain": true, "showOriginalLink": false, "spaceDomain": o.Subdomain}
	explicit := notionUUID.MatchString(o.Hint)
	if explicit {
		payload["blockId"] = o.Hint
	}
	request, err := NotionAPIRequest(o, "getPublicPageData", payload, false)
	if err != nil {
		return nil, err
	}
	data, status, err := fetch(ctx, request)
	if err != nil && status == 500 && !explicit {
		request, err = NotionAPIRequest(o, "getPublicPageData", payload, true)
		if err != nil {
			return nil, err
		}
		data, _, err = fetch(ctx, request)
	}
	if err != nil {
		return nil, err
	}
	if data == nil {
		return nil, ErrInventory
	}
	public := asEmbeddedObject(data.Value)
	space, _ := public["spaceId"].(string)
	home, _ := public["publicHomePage"].(string)
	if !notionUUID.MatchString(space) {
		return nil, ErrInventory
	}
	page := home
	if explicit {
		page = o.Hint
	} else if o.Hint != "" && home != "" {
		chunk, err := LoadNotionChunk(ctx, o, home, fetch)
		if err != nil {
			return nil, err
		}
		if id := notionFindSlug(chunk, o.Hint); id != "" {
			page = id
		}
	}
	candidates := []string{}
	if page != "" {
		candidates = append(candidates, page)
	}
	if home != "" && home != page {
		candidates = append(candidates, home)
	}
	for _, id := range candidates {
		chunk, err := LoadNotionChunk(ctx, o, id, fetch)
		if err != nil {
			return nil, err
		}
		pages, err := NotionChildPages(chunk, id, o.Nested)
		if err != nil {
			return nil, err
		}
		if len(pages) > 0 {
			pages, err = NotionFilterPages(pages, o, false)
			if err != nil {
				return nil, err
			}
			return notionInventoryURLs(pages, o)
		}
		collections, err := NotionCollectionViews(chunk)
		if err != nil {
			return nil, err
		}
		if o.CollectionIndex != nil && *o.CollectionIndex < len(collections) {
			collections = []NotionCollection{collections[*o.CollectionIndex]}
		}
		rows := []NotionPage{}
		for _, collection := range collections {
			payload := map[string]any{"source": map[string]any{"type": "collection", "id": collection.ID, "spaceId": space}, "collectionView": map[string]any{"id": collection.ViewID, "spaceId": space}, "loader": map[string]any{"type": "reducer", "reducers": map[string]any{"collection_group_results": map[string]any{"type": "results", "limit": 300}}, "searchQuery": "", "userTimeZone": "UTC"}}
			request, err := NotionAPIRequest(o, "queryCollection", payload, false)
			if err != nil {
				return nil, err
			}
			data, _, err := fetch(ctx, request)
			if err != nil {
				return nil, err
			}
			if data == nil {
				return nil, ErrInventory
			}
			page, err := NotionCollectionRows(data)
			if err != nil {
				return nil, err
			}
			for _, row := range page {
				if strings.TrimSpace(row.Title) != "" {
					rows = append(rows, row)
				}
			}
		}
		if len(rows) > 0 {
			rows, err = NotionFilterPages(rows, o, true)
			if err != nil {
				return nil, err
			}
			return notionInventoryURLs(rows, o)
		}
	}
	return []string{}, nil
}

func notionInventoryURLs(pages []NotionPage, o NotionOptions) ([]string, error) {
	urls := map[string]bool{}
	for _, page := range pages {
		source, err := NotionPageURL(o.Subdomain, page.ID)
		if err != nil {
			return nil, err
		}
		urls[source] = true
	}
	include, exclude := "", ""
	switch filter := o.URLFilter.(type) {
	case string:
		include = filter
	case map[string]any:
		include, _ = filter["include"].(string)
		exclude, _ = filter["exclude"].(string)
	}
	out := []string{}
	for source := range urls {
		keep := true
		for _, rule := range []struct {
			pattern string
			include bool
		}{{include, true}, {exclude, false}} {
			if rule.pattern == "" {
				continue
			}
			pattern, err := dom.CompileURLPattern(rule.pattern)
			if err != nil {
				return nil, err
			}
			match, err := pattern.FindStringMatch(source)
			if err != nil {
				return nil, err
			}
			if rule.include && match == nil || !rule.include && match != nil {
				keep = false
			}
		}
		if keep {
			out = append(out, source)
		}
	}
	sort.Strings(out)
	return out, nil
}
