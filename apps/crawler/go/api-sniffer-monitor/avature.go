package apisniffer

import (
	"context"
	"encoding/json"
	"golang.org/x/net/html"
	stdhtml "html"
	"net"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type AvatureBoard struct{ Host, Prefix, Page string }
type AvatureOptions struct {
	Board      AvatureBoard
	PortalID   string
	Configured bool
}
type AvaturePage struct {
	Board                     AvatureBoard
	PortalID                  string
	Total, Start, End         int
	HasTotal, Exact, HasRange bool
	Jobs                      map[string]string
	Next                      []string
}
type AvatureInventory struct {
	URLs      []string
	Truncated bool
	Metadata  map[string]any
}

var avatureID = regexp.MustCompile(`^[1-9][0-9]{0,19}$`)
var avatureVendor = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.avature\.net$`)
var avatureSegment = regexp.MustCompile(`^[^/?#\x00-\x20]{1,160}$`)
var avatureNumber = regexp.MustCompile(`([0-9][0-9,.\s]*)(\+)?`)
var avatureRange = regexp.MustCompile(`([0-9][0-9,.]*)\s*[-–]\s*([0-9][0-9,.]*)`)

func (b AvatureBoard) ListingURL() string { return "https://" + b.Host + b.Prefix + "/" + b.Page }
func avatureURL(raw string, vendorOnly bool) (*url.URL, string, bool) {
	u, e := url.Parse(raw)
	if e != nil || len(raw) > 8192 || !strings.EqualFold(u.Scheme, "https") || u.User != nil || u.Opaque != "" || u.Port() != "" && u.Port() != "443" {
		return nil, "", false
	}
	host := strings.TrimSuffix(strings.ToLower(u.Hostname()), ".")
	if vendorOnly && !avatureVendor.MatchString(host) || !vendorOnly && (host == "avature.net" || host == "www.avature.net" || host == "localhost" || host == "localhost.localdomain" || !strings.Contains(host, ".") || net.ParseIP(host) != nil) {
		return nil, "", false
	}
	return u, host, true
}
func avatureParts(path string) []string {
	out := []string{}
	for _, s := range strings.Split(path, "/") {
		if s != "" {
			out = append(out, s)
		}
	}
	return out
}
func avatureRoute(route string) (string, string) {
	switch strings.ToLower(route) {
	case "jobdetail":
		return "SearchJobs", "jobId"
	case "folderdetail":
		return "SearchJobs", "folderId"
	case "pipelinedetail":
		return "SearchJobsMaps", "pipelineId"
	}
	return "", ""
}
func AvatureBoardFromURL(raw string, allowCustom bool) (AvatureBoard, bool) {
	u, host, ok := avatureURL(raw, !allowCustom)
	if !ok {
		return AvatureBoard{}, false
	}
	parts := avatureParts(u.EscapedPath())
	if len(parts) == 0 {
		return AvatureBoard{}, false
	}
	for _, part := range parts {
		if !avatureSegment.MatchString(part) {
			return AvatureBoard{}, false
		}
	}
	for i, part := range parts {
		name := strings.ToLower(part)
		if name == "searchjobs" || name == "searchjobsmaps" {
			if i != len(parts)-1 || u.RawQuery != "" {
				return AvatureBoard{}, false
			}
			page := "SearchJobs"
			if name == "searchjobsmaps" {
				page = "SearchJobsMaps"
			}
			return AvatureBoard{host, "/" + strings.Join(parts[:i], "/"), page}.normalized(), true
		}
	}
	for i, part := range parts {
		page, key := avatureRoute(part)
		if page == "" {
			continue
		}
		if i == 0 {
			return AvatureBoard{}, false
		}
		tail := parts[i+1:]
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil {
			return AvatureBoard{}, false
		}
		if len(tail) > 0 {
			if len(tail) < 2 || !avatureID.MatchString(tail[len(tail)-1]) || u.RawQuery != "" {
				return AvatureBoard{}, false
			}
		} else if len(q) != 1 || len(q[key]) != 1 || !avatureID.MatchString(q.Get(key)) {
			return AvatureBoard{}, false
		}
		return AvatureBoard{host, "/" + strings.Join(parts[:i], "/"), page}.normalized(), true
	}
	return AvatureBoard{}, false
}
func (b AvatureBoard) normalized() AvatureBoard {
	b.Prefix = strings.TrimRight(b.Prefix, "/")
	return b
}
func AvatureOptionsFromMetadata(board, raw string) (AvatureOptions, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil {
		return AvatureOptions{}, ErrOptions
	}
	for k := range m {
		switch k {
		case "listing_url", "portal_id", "delist_threshold", "drop_threshold", "blast_radius_floor", "scraper_type", "scraper_config", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count":
		default:
			return AvatureOptions{}, ErrOptions
		}
	}
	b, ok := AvatureBoardFromURL(board, true)
	configured := false
	if listing, exists := m["listing_url"].(string); exists {
		if selected, valid := AvatureBoardFromURL(listing, true); valid {
			b, ok, configured = selected, true, true
		}
	}
	if !ok {
		return AvatureOptions{}, ErrOptions
	}
	portal := ""
	if raw, present := m["portal_id"]; present && raw != nil {
		switch value := raw.(type) {
		case string:
			portal = value
		case json.Number:
			portal = string(value)
		default:
			return AvatureOptions{}, ErrOptions
		}
		if e != nil || !avatureID.MatchString(portal) {
			return AvatureOptions{}, ErrOptions
		}
		n, e := strconv.ParseUint(portal, 10, 64)
		if e != nil || n == 0 {
			return AvatureOptions{}, ErrOptions
		}
		portal = strconv.FormatUint(n, 10)
	}
	return AvatureOptions{b, portal, configured}, nil
}
func AvaturePaginationURL(raw string, b AvatureBoard, allowZero bool) (string, int, bool) {
	base, _ := url.Parse(b.ListingURL())
	ref, e := url.Parse(raw)
	if e != nil {
		return "", 0, false
	}
	u, host, ok := avatureURL(base.ResolveReference(ref).String(), false)
	if !ok || host != b.Host || !strings.EqualFold(strings.TrimRight(u.EscapedPath(), "/"), base.EscapedPath()) {
		return "", 0, false
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil {
		return "", 0, false
	}
	sizeKey, offsetKey := "", ""
	for _, pair := range [][2]string{{"jobRecordsPerPage", "jobOffset"}, {"folderRecordsPerPage", "folderOffset"}, {"pipelineRecordsPerPage", "pipelineOffset"}, {"", "pipelineOffset"}} {
		expected := 2
		if pair[0] == "" {
			expected = 1
		}
		if len(q) == expected && len(q[pair[1]]) == 1 && (pair[0] == "" || len(q[pair[0]]) == 1) {
			sizeKey, offsetKey = pair[0], pair[1]
			break
		}
	}
	if offsetKey == "" {
		return "", 0, false
	}
	digits := regexp.MustCompile(`^[0-9]+$`)
	offsetRaw := q.Get(offsetKey)
	if !digits.MatchString(offsetRaw) {
		return "", 0, false
	}
	offset, e := strconv.Atoi(offsetRaw)
	if e != nil || offset < 0 || offset == 0 && !allowZero {
		return "", 0, false
	}
	query := offsetKey + "=" + strconv.Itoa(offset)
	if sizeKey != "" {
		value := q.Get(sizeKey)
		if !digits.MatchString(value) {
			return "", 0, false
		}
		size, e := strconv.Atoi(value)
		if e != nil || size <= 0 {
			return "", 0, false
		}
		query = sizeKey + "=" + strconv.Itoa(size) + "&" + query
	}
	return b.ListingURL() + "/?" + query, offset, true
}
func (o AvatureOptions) ResourceMatches(raw string) bool {
	if u, err := url.Parse(raw); err == nil && u.RawQuery == "" && u.Fragment == "" {
		base, _ := url.Parse(o.Board.ListingURL())
		if b, ok := AvatureBoardFromURL(raw, true); ok && strings.EqualFold(b.ListingURL(), o.Board.ListingURL()) && strings.EqualFold(strings.TrimRight(u.EscapedPath(), "/"), base.EscapedPath()) {
			return true
		}
	}
	canonical, offset, ok := AvaturePaginationURL(raw, o.Board, false)
	return ok && offset <= 50_000 && canonical == raw
}
func avatureDetail(raw string, b AvatureBoard) (string, string, bool) {
	u, host, ok := avatureURL(raw, false)
	if !ok || host != b.Host || !strings.HasPrefix(strings.ToLower(u.EscapedPath()), strings.ToLower(b.Prefix+"/")) {
		return "", "", false
	}
	parts := avatureParts(strings.Trim(u.EscapedPath()[len(b.Prefix):], "/"))
	if len(parts) == 0 {
		return "", "", false
	}
	_, key := avatureRoute(parts[0])
	if key == "" {
		return "", "", false
	}
	id, canonical := "", ""
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil {
		return "", "", false
	}
	if len(parts) == 1 {
		if len(q) != 1 || len(q[key]) != 1 || !avatureID.MatchString(q.Get(key)) {
			return "", "", false
		}
		id = q.Get(key)
		canonical = "https://" + b.Host + b.Prefix + "/" + parts[0] + "?" + key + "=" + url.QueryEscape(id)
	} else {
		if len(parts) < 3 || !avatureID.MatchString(parts[len(parts)-1]) {
			return "", "", false
		}
		id = parts[len(parts)-1]
		canonical = "https://" + b.Host + b.Prefix + "/" + strings.Join(parts, "/")
	}
	return strings.ToLower(parts[0]) + ":" + id, canonical, true
}
func avatureCleanInt(raw string) (int, bool) {
	s := strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, raw)
	n, e := strconv.Atoi(s)
	return n, e == nil
}
func ParseAvaturePage(body []byte, request string) (AvaturePage, error) {
	out := AvaturePage{Jobs: map[string]string{}, Next: []string{}}
	if len(body) > 2_000_000 {
		return out, ErrInventory
	}
	tree, e := eighthTree(body)
	if e != nil {
		return out, e
	}
	portal := map[string]string{}
	hint := request
	for _, node := range eighthSelect(tree, "meta") {
		name := strings.ToLower(eighthAttr(node, "name"))
		if strings.HasPrefix(name, "avature.portal.") {
			portal[strings.TrimPrefix(name, "avature.portal.")] = eighthAttr(node, "content")
		}
		if strings.EqualFold(eighthAttr(node, "property"), "og:url") && eighthAttr(node, "content") != "" {
			hint = eighthAttr(node, "content")
		}
	}
	hint = stdhtml.UnescapeString(hint)
	u, e := url.Parse(hint)
	if e != nil {
		return out, ErrInventory
	}
	clean := *u
	clean.RawQuery, clean.Fragment = "", ""
	b, ok := AvatureBoardFromURL(clean.String(), true)
	if !ok {
		return out, ErrInventory
	}
	if u.RawQuery != "" {
		if _, _, ok := AvaturePaginationURL(hint, b, true); !ok {
			return out, ErrInventory
		}
	}
	id := strings.TrimSpace(portal["id"])
	n, e := strconv.ParseUint(id, 10, 64)
	if e != nil || n == 0 || !strings.EqualFold(strings.TrimSpace(portal["page"]), b.Page) {
		return out, ErrInventory
	}
	out.Board, out.PortalID = b, strconv.FormatUint(n, 10)
	base, _ := url.Parse(b.ListingURL())
	for _, node := range eighthSelect(tree, "a[href]") {
		ref, e := url.Parse(eighthAttr(node, "href"))
		if e != nil {
			continue
		}
		identity, canonical, ok := avatureDetail(base.ResolveReference(ref).String(), b)
		if !ok {
			continue
		}
		old := out.Jobs[identity]
		if old == "" || strings.Contains(old, "?") && !strings.Contains(canonical, "?") {
			out.Jobs[identity] = canonical
		}
	}
	next := map[string]bool{}
	legendText := []string{}
	labels := []string{}
	var walk func(*html.Node, bool, bool)
	walk = func(node *html.Node, inNext, inLegend bool) {
		if node.Type == html.ElementNode {
			for _, class := range strings.Fields(strings.ToLower(eighthAttr(node, "class"))) {
				inNext = inNext || class == "paginationnextlink"
				inLegend = inLegend || class == "list-controls__text__legend" || class == "pagination__legend"
			}
			if inNext && node.Data == "a" {
				if canonical, _, ok := AvaturePaginationURL(eighthAttr(node, "href"), b, false); ok {
					next[canonical] = true
				}
			}
			if inLegend && eighthAttr(node, "aria-label") != "" {
				labels = append(labels, eighthAttr(node, "aria-label"))
			}
		}
		if inLegend && node.Type == html.TextNode && strings.TrimSpace(node.Data) != "" {
			legendText = append(legendText, strings.TrimSpace(node.Data))
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			walk(child, inNext, inLegend)
		}
	}
	walk(tree, false, false)
	for u := range next {
		out.Next = append(out.Next, u)
	}
	sort.Strings(out.Next)
	legend := strings.Join(legendText, " ")
	candidates := append(labels, legend)
	for _, value := range candidates {
		matches := avatureNumber.FindAllStringSubmatch(value, -1)
		if len(matches) == 0 {
			continue
		}
		last := matches[len(matches)-1]
		if n, ok := avatureCleanInt(last[1]); ok {
			out.Total, out.HasTotal, out.Exact = n, true, last[2] == ""
			break
		}
	}
	if m := avatureRange.FindStringSubmatch(legend); m != nil {
		a, aOK := avatureCleanInt(m[1])
		z, zOK := avatureCleanInt(m[2])
		out.Start, out.End, out.HasRange = a, z, aOK && zOK
	}
	return out, nil
}
func DiscoverAvature(ctx context.Context, o AvatureOptions, fetch func(context.Context, string) ([]byte, error)) (AvatureInventory, error) {
	empty := AvatureInventory{}
	raw, e := fetch(ctx, o.Board.ListingURL())
	if e != nil {
		return empty, e
	}
	first, e := ParseAvaturePage(raw, o.Board.ListingURL())
	if e != nil || first.Board.Page != o.Board.Page || o.Configured && !strings.EqualFold(first.Board.ListingURL(), o.Board.ListingURL()) || o.PortalID != "" && first.PortalID != o.PortalID {
		return empty, ErrInventory
	}
	b := first.Board
	out := AvatureInventory{URLs: []string{}, Metadata: map[string]any{"listing_url": b.ListingURL(), "portal_id": first.PortalID}}
	out.Truncated = first.HasTotal && first.Total > 50_000
	current := first
	expected, offset := 1, 0
	urls := map[string]string{}
	seen := map[string]bool{strings.ToLower(b.ListingURL()): true}
	for page := 1; page <= 10_000; page++ {
		if ctx.Err() != nil {
			return empty, ctx.Err()
		}
		if !current.HasTotal || current.Total == 0 && (len(current.Jobs) > 0 || len(current.Next) > 0) || current.Total > 0 && (!current.HasRange || current.Start != expected || current.End < current.Start || len(current.Jobs) != current.End-current.Start+1 || len(current.Next) > 1) || !strings.EqualFold(current.Board.ListingURL(), b.ListingURL()) || current.PortalID != first.PortalID {
			return empty, ErrInventory
		}
		if current.Total != first.Total || current.Exact != first.Exact {
			out.Truncated = true
		}
		ids := []string{}
		for id := range current.Jobs {
			if _, ok := urls[id]; ok {
				out.Truncated = true
			} else {
				ids = append(ids, id)
			}
		}
		sort.Strings(ids)
		for _, id := range ids {
			if len(urls) >= 50_000 {
				out.Truncated = true
				break
			}
			urls[id] = current.Jobs[id]
		}
		done := len(urls) >= 50_000 || page >= 10_000 || len(current.Next) == 0
		if done {
			if len(current.Next) > 0 || first.Exact && len(urls) != first.Total || !first.Exact && len(urls) <= first.Total {
				out.Truncated = true
			}
			for _, u := range urls {
				out.URLs = append(out.URLs, u)
			}
			sort.Strings(out.URLs)
			return out, ctx.Err()
		}
		next, nextOffset, ok := AvaturePaginationURL(current.Next[0], b, false)
		if !ok || nextOffset <= offset || current.HasRange && nextOffset != current.End || seen[strings.ToLower(next)] {
			return empty, ErrInventory
		}
		seen[strings.ToLower(next)] = true
		expected, offset = current.End+1, nextOffset
		raw, e = fetch(ctx, next)
		if e != nil {
			return empty, e
		}
		current, e = ParseAvaturePage(raw, next)
		if e != nil || current.Board.Page != b.Page {
			return empty, ErrInventory
		}
	}
	return empty, ErrInventory
}
