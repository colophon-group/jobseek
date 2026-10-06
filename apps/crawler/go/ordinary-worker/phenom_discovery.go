package worker

import (
	"bytes"
	"context"
	"encoding/xml"
	"errors"
	"io"
	"net/http"
	"sort"
	"strings"
	"time"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	bounded "github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/boundedhttp"
	sitemap "github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/sitemap"
)

type phenomXMLLocation struct {
	XMLName  xml.Name
	Location struct {
		XMLName xml.Name
		Text    string `xml:",chardata"`
	} `xml:"loc"`
}
type phenomXMLDocument struct {
	XMLName  xml.Name
	URLs     []phenomXMLLocation `xml:"url"`
	Children []phenomXMLLocation `xml:"sitemap"`
}

func phenomLocations(d *phenomXMLDocument, children bool) []string {
	entries := d.URLs
	if children {
		entries = d.Children
	}
	ns := d.XMLName.Space
	if ns == "" {
		ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
	}
	collect := func(space string) []string {
		out := []string{}
		for _, entry := range entries {
			if entry.XMLName.Space != space || entry.Location.XMLName.Space != space || entry.Location.Text == "" {
				continue
			}
			value := strings.TrimSpace(entry.Location.Text)
			if !children {
				value = sitemap.CanonicalURL(value)
			}
			out = append(out, value)
		}
		return out
	}
	out := collect(ns)
	if len(out) == 0 {
		return collect("")
	}
	return out
}

func phenomChildren(children []string, languages []string) []string {
	language := func(resource string) string {
		name := resource[strings.LastIndex(resource, "/")+1:]
		if !strings.HasSuffix(name, ".xml") {
			return ""
		}
		parts := strings.Split(strings.TrimSuffix(name, ".xml"), "-")
		if len(parts) < 3 {
			return ""
		}
		return strings.ToLower(strings.Join(parts[2:], "-"))
	}
	seen := map[string]bool{}
	for _, child := range children {
		if lang := language(child); lang != "" {
			seen[lang] = true
		}
	}
	if len(seen) <= 1 {
		return children
	}
	keep := map[string]bool{"": true}
	for _, lang := range languages {
		if lang != "" {
			keep[strings.ToLower(lang)] = true
		}
	}
	selected := []string{}
	for _, child := range children {
		if keep[language(child)] {
			selected = append(selected, child)
		}
	}
	return selected
}

// Root discovery is a single lenient request. A selected child shard consumes
// the existing three-attempt retry budget and fails the entire inventory on a
// persistent transient response. No partial union is published on that path.
func fetchPhenomXML(ctx context.Context, s *nativeSitemapSession, resource string, child bool) (*phenomXMLDocument, error) {
	attempts := 1
	if child {
		attempts = 3
	}
	for attempt := 0; attempt < attempts; attempt++ {
		r, err := s.Get(ctx, resource, http.Header{"User-Agent": {"jobseek-crawler (+https://jseek.co/)"}, "Accept": {"application/xml,text/xml,*/*;q=0.8"}})
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if s.response != nil && s.response.reserved {
			return nil, &DiscoveryError{Kind: "publisher_reserved", cause: err}
		}
		var transport *bounded.Error
		retry := r.StatusCode == 200 && len(r.Body) == 0 || r.StatusCode == 202 || r.StatusCode == 401 || r.StatusCode == 403 || r.StatusCode == 408 || r.StatusCode == 425 || r.StatusCode == 429 || r.StatusCode >= 500 && r.StatusCode <= 599
		if err != nil {
			retry = errors.As(err, &transport) && (transport.Kind == bounded.ErrorTransport || transport.Kind == bounded.ErrorTimeout)
			if !retry {
				return nil, err
			}
		}
		if !child && (err != nil || r.StatusCode != 200 || !strings.Contains(r.Header.Get("Content-Type"), "xml")) {
			return nil, nil
		}
		if child && retry {
			if attempt == attempts-1 {
				return nil, &DiscoveryError{Kind: "inventory_failed", cause: err}
			}
			if !waitRuntime(ctx, 500*time.Millisecond*time.Duration(1<<attempt)) {
				return nil, ctx.Err()
			}
			continue
		}
		if r.StatusCode != 200 {
			return nil, nil
		}
		decoder := xml.NewDecoder(bytes.NewReader(r.Body))
		var doc phenomXMLDocument
		if decoder.Decode(&doc) != nil {
			return nil, nil
		}
		for {
			token, trailing := decoder.Token()
			if trailing == io.EOF {
				break
			}
			if trailing != nil {
				return nil, nil
			}
			switch value := token.(type) {
			case xml.Comment, xml.ProcInst:
			case xml.CharData:
				if len(bytes.TrimSpace(value)) != 0 {
					return nil, nil
				}
			default:
				return nil, nil
			}
		}
		if child {
			kind := strings.ToLower(doc.XMLName.Local)
			if !strings.HasSuffix(kind, "urlset") && !strings.HasSuffix(kind, "sitemapindex") {
				return nil, nil
			}
		}
		return &doc, nil
	}
	panic("unreachable")
}

func discoverPhenomInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	root, languages, exclude, err := queue.PhenomMonitorConfig(config)
	if err != nil || client == nil || p.Provider != "phenom" || (p.Profile != "phenom.sitemap-urls/v1" && p.Profile != "phenom.proxy-sitemap-urls/v1") || root != p.Endpoint {
		return result, queue.ErrConfiguration
	}
	op := *client
	op.Jar = nil
	op.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	// At most 1,000 selected shards (including grandchildren), each with the
	// existing three attempts. The task deadline and shared body cap still apply.
	session := &nativeSitemapSession{client: &op, endpoint: root, maxRequests: 3001, successfulPolicyOnly: true}
	doc, err := fetchPhenomXML(ctx, session, root, false)
	finish := func(err error) (RichDiscovery, error) { result.Response = session.response; return result, err }
	if err != nil || doc == nil {
		return finish(err)
	}
	urls := map[string]bool{}
	add := func(d *phenomXMLDocument) {
		for _, u := range phenomLocations(d, false) {
			urls[u] = true
		}
	}
	if !strings.Contains(strings.ToLower(doc.XMLName.Local), "sitemapindex") {
		add(doc)
	} else {
		for _, child := range phenomChildren(phenomLocations(doc, true), languages) {
			if len(urls) >= 50_000 {
				result.Truncated = true
				break
			}
			leaf, err := fetchPhenomXML(ctx, session, child, true)
			if err != nil {
				return finish(err)
			}
			if leaf == nil {
				continue
			}
			if !strings.Contains(strings.ToLower(leaf.XMLName.Local), "sitemapindex") {
				add(leaf)
				continue
			}
			for _, grandchild := range phenomChildren(phenomLocations(leaf, true), languages) {
				if len(urls) >= 50_000 {
					result.Truncated = true
					break
				}
				last, err := fetchPhenomXML(ctx, session, grandchild, true)
				if err != nil {
					return finish(err)
				}
				if last != nil {
					add(last)
				}
			}
		}
	}
	pattern, err := dom.CompileURLPattern(exclude)
	if err != nil {
		return finish(err)
	}
	ordered := []string{}
	for u := range urls {
		if ctx.Err() != nil {
			return finish(ctx.Err())
		}
		lower := strings.ToLower(u)
		if !strings.Contains(lower, "/job/") && !strings.Contains(lower, "?job_id=") && !strings.Contains(lower, "&job_id=") {
			continue
		}
		if exclude != "" {
			match, err := pattern.MatchString(u)
			if err != nil {
				return finish(err)
			}
			if match {
				continue
			}
		}
		ordered = append(ordered, u)
	}
	sort.Strings(ordered)
	for _, u := range ordered {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: u})
	}
	return finish(nil)
}
