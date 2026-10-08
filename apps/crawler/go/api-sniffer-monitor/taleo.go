package apisniffer

import (
	"context"
	"encoding/json"
	stdhtml "html"
	"io"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/net/html"
)

type TaleoBoard struct {
	Host, Partition, Org string
	CWS                  int64
}
type TaleoOptions struct {
	Board      TaleoBoard
	Configured bool
}
type TaleoPage struct {
	Total, Next *int
	URLs        []string
}

var taleoHost = regexp.MustCompile(`^[a-z]{3}\.tbe\.taleo\.net$`)
var taleoPartition = regexp.MustCompile(`^[a-z]{3}[0-9]{2}$`)
var taleoOrg = regexp.MustCompile(`^[A-Z0-9][A-Z0-9_-]{0,63}$`)
var taleoID = regexp.MustCompile(`^[1-9][0-9]{0,9}$`)
var taleoPath = regexp.MustCompile(`(?i)^/([a-z]{3}[0-9]{2})/ats/careers/v2/(searchResults|viewRequisition)/?$`)

func taleoPositive(v any) (int64, bool) {
	var s string
	switch x := v.(type) {
	case string:
		s = strings.TrimSpace(x)
	case json.Number:
		s = string(x)
	default:
		return 0, false
	}
	if !taleoID.MatchString(s) {
		return 0, false
	}
	n, e := strconv.ParseInt(s, 10, 64)
	return n, e == nil
}
func taleoIdentity(host, partition, org string, cws any) (TaleoBoard, bool) {
	b := TaleoBoard{Host: strings.ToLower(strings.TrimSpace(host)), Partition: strings.ToLower(strings.TrimSpace(partition)), Org: strings.ToUpper(strings.TrimSpace(org))}
	n, ok := taleoPositive(cws)
	b.CWS = n
	return b, ok && taleoHost.MatchString(b.Host) && taleoPartition.MatchString(b.Partition) && strings.HasPrefix(b.Partition, b.Host[:3]) && taleoOrg.MatchString(b.Org)
}
func (b TaleoBoard) ListingURL(offset *int) string {
	u := "https://" + b.Host + "/" + b.Partition + "/ats/careers/v2/searchResults?org=" + url.QueryEscape(b.Org) + "&cws=" + strconv.FormatInt(b.CWS, 10)
	if offset != nil {
		u += "&rowFrom=" + strconv.Itoa(*offset)
	}
	return u
}
func (b TaleoBoard) JobURL(id int64) string {
	return "https://" + b.Host + "/" + b.Partition + "/ats/careers/v2/viewRequisition?org=" + url.QueryEscape(b.Org) + "&cws=" + strconv.FormatInt(b.CWS, 10) + "&rid=" + strconv.FormatInt(id, 10)
}

func ParseTaleoURL(raw string) (TaleoBoard, bool, int64, *int, error) {
	fail := func() (TaleoBoard, bool, int64, *int, error) { return TaleoBoard{}, false, 0, nil, ErrOptions }
	if len(raw) > 4096 {
		return fail()
	}
	u, e := url.Parse(stdhtml.UnescapeString(raw))
	if e != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Fragment != "" || u.Port() != "" && u.Port() != "443" {
		return fail()
	}
	m := taleoPath.FindStringSubmatch(u.Path)
	q, e := url.ParseQuery(u.RawQuery)
	if m == nil || e != nil || len(q) > 8 {
		return fail()
	}
	for _, v := range q {
		if len(v) != 1 {
			return fail()
		}
	}
	b, ok := taleoIdentity(u.Hostname(), m[1], q.Get("org"), q.Get("cws"))
	if !ok {
		return fail()
	}
	detail := strings.EqualFold(m[2], "viewRequisition")
	if detail {
		if len(q) != 3 {
			return fail()
		}
		id, ok := taleoPositive(q.Get("rid"))
		if !ok {
			return fail()
		}
		return b, true, id, nil, nil
	}
	for k := range q {
		if k != "org" && k != "cws" && k != "rowFrom" {
			return fail()
		}
	}
	var offset *int
	if v, exists := q["rowFrom"]; exists {
		n, e := taleoDecimalText(v[0])
		if e != nil || n < 0 || n > 49990 || n%10 != 0 {
			return fail()
		}
		offset = &n
	}
	return b, false, 0, offset, nil
}

func TaleoOptionsFromMetadata(board, raw string) (TaleoOptions, error) {
	o := TaleoOptions{}
	md, e := DecodeInlineMetadata(raw)
	if e != nil {
		return o, e
	}
	for k := range md {
		switch k {
		case "host", "partition", "org", "cws", "scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count":
		default:
			return o, ErrOptions
		}
	}
	direct, _, _, _, directErr := ParseTaleoURL(board)
	configured := false
	for _, k := range []string{"host", "partition", "org", "cws"} {
		if _, ok := md[k]; ok {
			configured = true
		}
	}
	if configured {
		h, hOK := md["host"].(string)
		p, pOK := md["partition"].(string)
		org, oOK := md["org"].(string)
		b, ok := taleoIdentity(h, p, org, md["cws"])
		if !hOK || !pOK || !oOK || !ok || directErr == nil && direct.Org != b.Org {
			return o, ErrOptions
		}
		o.Board, o.Configured = b, true
	} else {
		if directErr != nil {
			return o, ErrOptions
		}
		o.Board = direct
	}
	return o, nil
}

func TaleoSafeRedirect(b TaleoBoard, resource, location string) (string, TaleoBoard, error) {
	if location == "" {
		return "", b, ErrOptions
	}
	target, e := PythonJoinURL(resource, stdhtml.UnescapeString(location))
	if e != nil {
		return "", b, ErrOptions
	}
	listing, detail, _, offset, e := ParseTaleoURL(target)
	if e == nil && !detail && offset == nil && listing.Org == b.Org {
		return listing.ListingURL(nil), listing, nil
	}
	u, e := url.Parse(target)
	if e != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" || !taleoHost.MatchString(strings.ToLower(u.Hostname())) || u.Path != "/dispatcher/servlet/DispatcherServlet" {
		return "", b, ErrOptions
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q) != 3 {
		return "", b, ErrOptions
	}
	for _, v := range q {
		if len(v) != 1 {
			return "", b, ErrOptions
		}
	}
	embedded, detail, _, offset, e := ParseTaleoURL(q.Get("redirectUrl"))
	if e != nil || detail || offset != nil || embedded != b || strings.ToUpper(strings.TrimSpace(q.Get("org"))) != b.Org || q.Get("act") != "redirectCws" {
		return "", b, ErrOptions
	}
	return target, b, nil
}

func TaleoInactiveRedirect(b TaleoBoard, resource, location string) bool {
	const prefix = "INACTIVEcareers/v2/searchResults?"
	if !strings.HasPrefix(location, prefix) {
		return false
	}
	resolved, _, err := TaleoSafeRedirect(b, b.ListingURL(nil), resource)
	if err != nil || resolved != resource {
		return false
	}
	q, err := url.ParseQuery(strings.TrimPrefix(location, prefix))
	if err != nil || len(q) != 2 || len(q["org"]) != 1 || len(q["cws"]) != 1 {
		return false
	}
	cws, ok := taleoPositive(q.Get("cws"))
	return ok && cws == b.CWS && strings.ToUpper(strings.TrimSpace(q.Get("org"))) == b.Org
}

func (o TaleoOptions) ResourceMatches(resource string) bool {
	b, detail, _, offset, err := ParseTaleoURL(resource)
	if err == nil && b == o.Board && !detail {
		return resource == o.Board.ListingURL(offset)
	}
	resolved, b, err := TaleoSafeRedirect(o.Board, o.Board.ListingURL(nil), resource)
	return err == nil && b == o.Board && resolved == resource
}

func (o TaleoOptions) FirstResourceMatches(resource string) bool {
	if resource == o.Board.ListingURL(nil) {
		return true
	}
	u, err := url.Parse(resource)
	return err == nil && u.Path == "/dispatcher/servlet/DispatcherServlet" && o.ResourceMatches(resource)
}

func taleoDecimalText(raw string) (int, error) {
	if raw == "" {
		return 0, ErrInventory
	}
	s := strings.Builder{}
	for _, r := range raw {
		if !unicode.Is(unicode.Nd, r) {
			return 0, ErrInventory
		}
		digit := -1
		for _, v := range unicode.Nd.R16 {
			if r >= rune(v.Lo) && r <= rune(v.Hi) && (r-rune(v.Lo))%rune(v.Stride) == 0 {
				digit = int((r-rune(v.Lo))/rune(v.Stride)) % 10
				break
			}
		}
		if digit < 0 {
			for _, v := range unicode.Nd.R32 {
				if uint32(r) >= v.Lo && uint32(r) <= v.Hi && (uint32(r)-v.Lo)%v.Stride == 0 {
					digit = int((uint32(r)-v.Lo)/v.Stride) % 10
					break
				}
			}
		}
		if digit < 0 {
			return 0, ErrInventory
		}
		s.WriteByte(byte('0' + digit))
	}
	n, e := strconv.Atoi(s.String())
	return n, e
}

func ParseTaleoPage(body []byte, b TaleoBoard, offset int) (TaleoPage, error) {
	out := TaleoPage{URLs: []string{}}
	if len(body) > 8_000_000 || utf8.RuneCount(body) > 2_000_000 || offset < 0 || offset%10 != 0 {
		return out, ErrInventory
	}
	z := html.NewTokenizer(strings.NewReader(string(body)))
	depth := 0
	text := strings.Builder{}
	totals := map[int]bool{}
	jobs := map[int64]bool{}
	next := []string{}
	start := func(t html.Token) error {
		if depth > 0 {
			depth++
		} else if t.Data == "span" {
			for _, a := range t.Attr {
				if a.Key == "class" {
					for _, c := range strings.Fields(a.Val) {
						if c == "oracletaleocwsv2-panel-number" {
							depth = 1
							text.Reset()
						}
					}
					break
				}
			}
		}
		if t.Data != "a" {
			return nil
		}
		attrs := map[string]string{}
		for _, a := range t.Attr {
			attrs[a.Key] = a.Val
		}
		for _, c := range strings.Fields(attrs["class"]) {
			if c == "jscroll-next" {
				next = append(next, attrs["href"])
				break
			}
		}
		absolute, e := PythonJoinURL(b.ListingURL(&offset), attrs["href"])
		if e == nil {
			board, detail, id, _, e := ParseTaleoURL(absolute)
			if e == nil && detail && board == b {
				jobs[id] = true
			}
		}
		return nil
	}
	end := func() error {
		if depth == 0 {
			return nil
		}
		depth--
		if depth > 0 {
			return nil
		}
		raw := strings.ReplaceAll(strings.TrimSpace(text.String()), ",", "")
		n, e := taleoDecimalText(raw)
		if e == nil {
			totals[n] = true
		} else if raw != "" {
			// A numeric total that exceeds the integer bound is incomplete
			// evidence, not a no-total empty listing.
			numeric := true
			for _, r := range raw {
				if !unicode.IsNumber(r) {
					numeric = false
					break
				}
			}
			if numeric {
				return ErrInventory
			}
		}
		return nil
	}
	for {
		switch z.Next() {
		case html.ErrorToken:
			if z.Err() != io.EOF {
				return out, ErrInventory
			}
			goto parsed
		case html.StartTagToken:
			if start(z.Token()) != nil {
				return out, ErrInventory
			}
		case html.SelfClosingTagToken:
			if start(z.Token()) != nil || end() != nil {
				return out, ErrInventory
			}
		case html.EndTagToken:
			if end() != nil {
				return out, ErrInventory
			}
		case html.TextToken:
			if depth > 0 {
				text.Write(z.Text())
			}
		}
	}
parsed:
	for id := range jobs {
		out.URLs = append(out.URLs, b.JobURL(id))
	}
	sort.Strings(out.URLs)
	if len(totals) == 1 {
		for n := range totals {
			out.Total = &n
		}
		if len(jobs) != min(10, max(0, *out.Total-offset)) {
			return out, ErrInventory
		}
		return out, nil
	}
	if !strings.Contains(strings.ToLower(string(body)), "oracletaleocwsv2") || len(jobs) > 10 {
		return out, ErrInventory
	}
	for _, href := range next {
		if href == "" {
			return out, ErrInventory
		}
		absolute, e := PythonJoinURL(b.ListingURL(nil), href)
		if e != nil {
			return out, ErrInventory
		}
		u, e := url.Parse(absolute)
		if e != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" || strings.ToLower(u.Hostname()) != b.Host || strings.TrimRight(u.Path, "/") != "/"+b.Partition+"/ats/careers/v2/searchResults" {
			return out, ErrInventory
		}
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil || len(q) > 8 || len(q["next"]) != 1 || q.Get("next") != "" || len(q["rowFrom"]) != 1 {
			return out, ErrInventory
		}
		for k, v := range q {
			if len(v) != 1 {
				return out, ErrInventory
			}
			switch k {
			case "next", "rowFrom":
			case "act", "sortColumn", "sortOrder":
				if v[0] != "null" {
					return out, ErrInventory
				}
			case "currentTime":
				if _, e := taleoDecimalText(v[0]); e != nil {
					return out, ErrInventory
				}
			default:
				return out, ErrInventory
			}
		}
		n, e := taleoDecimalText(q.Get("rowFrom"))
		if e != nil || n < 10 || n > 50000 || n != offset+10 || out.Next != nil && *out.Next != n {
			return out, ErrInventory
		}
		out.Next = &n
	}
	if out.Next != nil && len(jobs) != 10 {
		return out, ErrInventory
	}
	return out, nil
}

func DiscoverTaleo(ctx context.Context, o TaleoOptions, fetch func(context.Context, string) ([]byte, error)) ([]string, error) {
	first, e := fetch(ctx, o.Board.ListingURL(nil))
	if e != nil {
		return nil, e
	}
	page, e := ParseTaleoPage(first, o.Board, 0)
	if e != nil || page.Total != nil && *page.Total > 50000 {
		return nil, ErrInventory
	}
	urls := map[string]bool{}
	for _, u := range page.URLs {
		urls[u] = true
	}
	total := page.Total
	for offset := 10; total != nil && offset < *total || total == nil && page.Next != nil; offset += 10 {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if total == nil {
			offset = *page.Next
		}
		if offset > 49990 || len(urls) >= 50000 {
			return nil, ErrInventory
		}
		body, e := fetch(ctx, o.Board.ListingURL(&offset))
		if e != nil {
			return nil, e
		}
		page, e = ParseTaleoPage(body, o.Board, offset)
		if e != nil || total != nil && (page.Total == nil || *page.Total != *total) || total == nil && (page.Total != nil || len(page.URLs) == 0) {
			return nil, ErrInventory
		}
		for _, u := range page.URLs {
			if urls[u] {
				return nil, ErrInventory
			}
			urls[u] = true
		}
	}
	if total != nil && len(urls) != *total {
		return nil, ErrInventory
	}
	out := []string{}
	for u := range urls {
		out = append(out, u)
	}
	sort.Strings(out)
	return out, nil
}
