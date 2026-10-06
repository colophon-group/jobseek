package apisniffer

import (
	"net/url"
	"strconv"
	"strings"
)

type EightfoldOptions struct {
	Origin, SitemapURL string
	Metadata           map[string]any
}

func (o EightfoldOptions) ForceFull() bool { return detailTruthy(o.Metadata["pcsx_force_full_crawl"]) }

func EightfoldOptionsFromMetadata(source, metadata string) (EightfoldOptions, error) {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || u.RawPath != "" || u.Fragment != "" {
		return EightfoldOptions{}, ErrOptions
	}
	d, e := Decode([]byte(metadata))
	if e != nil {
		return EightfoldOptions{}, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return EightfoldOptions{}, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(m[key]) {
			return EightfoldOptions{}, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return EightfoldOptions{}, ErrOptions
	}
	origin := "https://" + strings.ToLower(u.Hostname())
	sitemap := origin + "/careers/sitemap.xml"
	if detailTruthy(m["sitemap_url"]) {
		var ok bool
		sitemap, ok = m["sitemap_url"].(string)
		if !ok {
			return EightfoldOptions{}, ErrOptions
		}
	}
	v, e := url.Parse(sitemap)
	if e != nil || !validURL(sitemap) || v.Host != u.Host || v.Fragment != "" || v.RawPath != "" {
		return EightfoldOptions{}, ErrOptions
	}
	if _, e := EightfoldReadWatermark(m); e != nil {
		return EightfoldOptions{}, e
	}
	return EightfoldOptions{origin, sitemap, m}, nil
}

func (o EightfoldOptions) SearchURL(domain string, offset, num int) string {
	// Match the existing httpx query order for retry/pagination evidence.
	return o.Origin + "/api/pcsx/search?domain=" + url.QueryEscape(domain) + "&query=&location=&start=" + strconv.Itoa(offset) + "&num=" + strconv.Itoa(num)
}
func (o EightfoldOptions) ResourceMatches(source string) bool {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || u.Fragment != "" || u.RawPath != "" {
		return false
	}
	root, e := url.Parse(o.Origin)
	if e != nil || u.Host != root.Host {
		return false
	}
	if u.Path != "/api/pcsx/search" {
		return true
	} // bounded sitemap traversal on this origin
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q) != 5 || len(q["domain"]) != 1 || q.Get("domain") == "" || len(q.Get("domain")) > 256 || len(q["query"]) != 1 || q.Get("query") != "" || len(q["location"]) != 1 || q.Get("location") != "" || len(q["start"]) != 1 || len(q["num"]) != 1 {
		return false
	}
	n, e := strconv.Atoi(q.Get("num"))
	if e != nil || n != 1 && n != 10 {
		return false
	}
	offset, e := strconv.Atoi(q.Get("start"))
	return e == nil && offset >= 0 && offset <= 49990 && offset%10 == 0 && strconv.Itoa(offset) == q.Get("start")
}
