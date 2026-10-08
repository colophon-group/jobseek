package apisniffer

import (
	"context"
	"math"
	"sort"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"github.com/dlclark/regexp2/v2"
)

func htmlURLPattern(pattern string) (*regexp2.Regexp, error) {
	if pattern == "" {
		pattern = `href=["']([^"'#][^"']*)["']`
	}
	re, err := dom.CompileURLPattern(pattern)
	if err != nil {
		return nil, ErrOptions
	}
	groups := re.GetGroupNumbers()
	if len(groups) < 2 || groups[1] != 1 {
		return nil, ErrOptions
	}
	return re, nil
}

// JSON APIs that return HTML retain the original URL-set pagination: union
// identities, stop on no growth or a legitimate tail, and discard transient
// later-page failures. They never infer rich fields from an unexpected array.
func discoverHTML(ctx context.Context, o Options, first *Document, fetch Fetch, join JoinURL) (Inventory, error) {
	result := Inventory{Jobs: []Job{}, URLOnly: true}
	re, err := htmlURLPattern(o.URLRegex)
	if err != nil {
		return result, err
	}
	urls := map[string]bool{}
	add := func(content string) error {
		if len(content) > 64<<20 {
			return ErrInventory
		}
		match, err := re.FindStringMatch(content)
		for match != nil && err == nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			group := match.GroupByNumber(1)
			if group == nil || len(group.Captures) == 0 {
				return ErrInventory
			}
			raw := group.String()
			if !strings.HasPrefix(raw, "javascript:") && !strings.HasPrefix(raw, "mailto:") {
				joined, err := join(o.BoardURL, raw)
				if err != nil {
					return err
				}
				urls[joined] = true
				if len(urls) > 2000000 {
					return ErrInventory
				}
			}
			match, err = re.FindNextMatch(match)
		}
		if err != nil {
			return ErrInventory
		}
		return ctx.Err()
	}
	value, err := Search(first.Value, o.Path)
	if err != nil {
		return result, ErrInventory
	}
	content, ok := value.(string)
	if !ok {
		// Only the configured empty document can establish absence.
		matches, err := first.matchesEmptyResponse(o.EmptyResponse, true)
		if len(o.EmptyResponse) > 0 && err == nil && matches {
			return result, nil
		}
		return result, ErrInventory
	}
	if err := add(content); err != nil {
		return result, err
	}
	if len(urls) == 0 && len(o.EmptyResponse) > 0 {
		matches, err := first.matchesEmptyResponse(o.EmptyResponse, true)
		if err != nil || !matches {
			return result, ErrInventory
		}
	}
	total, known := first.total(o.Path, o.TotalPath)
	if pg := o.Pagination; pg != nil && len(urls) > 0 {
		size := pg.PageSize
		if size == 0 {
			size = len(urls)
		}
		pages := pg.MaxPages
		if known && total > 0 {
			pages = min(pages, (total+size-1)/size)
		}
		for index := 1; index < pages; index++ {
			if ctx.Err() != nil {
				return result, ctx.Err()
			}
			request, err := requestAt(o, pg, pg.Start+index*pg.Increment)
			if err != nil {
				return result, err
			}
			page, err := fetch(ctx, request)
			if err != nil {
				return result, err
			}
			if ctx.Err() != nil {
				return result, ctx.Err()
			}
			if page == nil {
				break
			}
			value, err := Search(page.Value, o.Path)
			if err != nil {
				return result, ErrInventory
			}
			content, ok := value.(string)
			if !ok || strings.TrimSpace(content) == "" {
				break
			}
			previous := len(urls)
			if err := add(content); err != nil {
				return result, err
			}
			if len(urls) == previous {
				break
			}
		}
	}
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	ordered := make([]string, 0, len(urls))
	for raw := range urls {
		ordered = append(ordered, raw)
	}
	sort.Strings(ordered)
	for _, raw := range ordered {
		result.Jobs = append(result.Jobs, Job{URL: raw, Metadata: map[string]any{}, Extras: map[string]any{}})
	}
	result.Truncated = known && total > 0 && total-len(urls) > max(1, int(math.Ceil(float64(total)*0.01)))
	return result, nil
}
