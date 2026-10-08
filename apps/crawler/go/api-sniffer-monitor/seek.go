package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type SeekOptions struct{ Host, Advertiser, APIHost, JobHost, SiteKey string }

var seekMarkets = map[string][3]string{
	"au.seek.com":     {"www.seek.com.au", "au.seek.com", "AU-Main"},
	"www.seek.com.au": {"www.seek.com.au", "au.seek.com", "AU-Main"},
	"nz.seek.com":     {"nz.seek.com", "nz.seek.com", "NZ-Main"},
	"www.seek.co.nz":  {"nz.seek.com", "nz.seek.com", "NZ-Main"},
}
var seekID = regexp.MustCompile(`^[\p{Nd}]{1,18}$`)

func SeekOptionsFromMetadata(board, raw string) (SeekOptions, error) {
	o := SeekOptions{}
	u, e := url.Parse(board)
	if e != nil || !strings.EqualFold(u.Scheme, "https") || u.User != nil || u.Opaque != "" || u.Fragment != "" || u.Port() != "" && u.Port() != "443" || strings.TrimRight(u.Path, "/") != "/jobs" {
		return o, ErrOptions
	}
	market, ok := seekMarkets[strings.ToLower(u.Hostname())]
	q, e := url.ParseQuery(u.RawQuery)
	if !ok || e != nil || len(q) != 1 || len(q["advertiserid"]) != 1 || !seekID.MatchString(q.Get("advertiserid")) {
		return o, ErrOptions
	}
	m, e := DecodeInlineMetadata(raw)
	if e != nil {
		return o, ErrOptions
	}
	for k := range m {
		switch k {
		case "host", "advertiser_id", "scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count":
		default:
			return o, ErrOptions
		}
	}
	_, hasHost := m["host"]
	_, hasID := m["advertiser_id"]
	if hasHost || hasID {
		h, hOK := m["host"].(string)
		id, iOK := m["advertiser_id"].(string)
		configured, cOK := seekMarkets[h]
		if !hOK || !iOK || !cOK || !seekID.MatchString(id) || configured[1] != market[1] || id != q.Get("advertiserid") {
			return o, ErrOptions
		}
	}
	return SeekOptions{strings.ToLower(u.Hostname()), q.Get("advertiserid"), market[0], market[1], market[2]}, nil
}
func (o SeekOptions) PageURL(page int) string {
	q := url.Values{"advertiserid": {o.Advertiser}, "page": {strconv.Itoa(page)}, "pagesize": {"100"}, "siteKey": {o.SiteKey}}
	return "https://" + o.APIHost + "/api/jobsearch/v5/search?" + q.Encode()
}
func (o SeekOptions) ResourceMatches(resource string) bool {
	u, e := url.Parse(resource)
	if e != nil {
		return false
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q["page"]) != 1 {
		return false
	}
	page, e := strconv.Atoi(q.Get("page"))
	return e == nil && page >= 1 && page <= 500 && resource == o.PageURL(page)
}
func seekNonnegativeInteger(v any) (int, error) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, ErrInventory
	}
	i, e := strconv.ParseInt(string(n), 10, 64)
	if e != nil || i < 0 || i > 50_000 {
		return 0, ErrInventory
	}
	return int(i), nil
}
func ParseSeekPage(body []byte, advertiser string, page int) ([]string, int, error) {
	if len(body) > 2_000_000 || page < 1 || page > 500 {
		return nil, 0, ErrInventory
	}
	d, e := Decode(body)
	if e != nil {
		return nil, 0, ErrInventory
	}
	p, ok := d.Value.(map[string]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	rows, ok := p["data"].([]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	total, e := seekNonnegativeInteger(p["totalCount"])
	if e != nil {
		return nil, 0, e
	}
	md, ok := p["solMetadata"].(map[string]any)
	if !ok || md["advertiser"] != advertiser {
		return nil, 0, ErrInventory
	}
	reportedPage, e1 := seekNonnegativeInteger(md["pageNumber"])
	size, e2 := seekNonnegativeInteger(md["pageSize"])
	reportedTotal, e3 := seekNonnegativeInteger(md["totalJobCount"])
	if e1 != nil || e2 != nil || e3 != nil || reportedPage != page || size != 100 || reportedTotal != total || len(rows) != min(100, max(0, total-(page-1)*100)) {
		return nil, 0, ErrInventory
	}
	ids := []string{}
	seen := map[string]bool{}
	scalar := func(v any) string {
		switch x := v.(type) {
		case string:
			return x
		case json.Number:
			if x == "0" {
				return ""
			}
			return string(x)
		}
		return ""
	}
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			return nil, 0, ErrInventory
		}
		id := scalar(row["id"])
		owner, ok := row["advertiser"].(map[string]any)
		if !ok || !seekID.MatchString(id) || scalar(owner["id"]) != advertiser || seen[id] {
			return nil, 0, ErrInventory
		}
		seen[id] = true
		ids = append(ids, id)
	}
	return ids, total, nil
}
func DiscoverSeek(ctx context.Context, o SeekOptions, fetch func(context.Context, string) ([]byte, error)) ([]string, error) {
	first, e := fetch(ctx, o.PageURL(1))
	if e != nil {
		return nil, e
	}
	ids, total, e := ParseSeekPage(first, o.Advertiser, 1)
	if e != nil {
		return nil, e
	}
	seen := map[string]bool{}
	for _, id := range ids {
		seen[id] = true
	}
	for page := 2; page <= (total+99)/100; page++ {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		body, e := fetch(ctx, o.PageURL(page))
		if e != nil {
			return nil, e
		}
		following, currentTotal, e := ParseSeekPage(body, o.Advertiser, page)
		if e != nil || currentTotal != total {
			return nil, ErrInventory
		}
		for _, id := range following {
			if seen[id] {
				return nil, ErrInventory
			}
			seen[id] = true
			ids = append(ids, id)
		}
	}
	if len(ids) != total {
		return nil, ErrInventory
	}
	jobs := []string{}
	for _, id := range ids {
		jobs = append(jobs, "https://"+o.JobHost+"/job/"+id)
	}
	return jobs, ctx.Err()
}
