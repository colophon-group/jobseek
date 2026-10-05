package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"mime"
	"net/http"
	"net/http/cookiejar"
	"sort"
	"strings"
	"time"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"golang.org/x/text/cases"
)

func discoverDOMInventory(ctx context.Context, verified *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	c, err := queue.DOMMonitorOptions(config)
	if err != nil || verified == nil || profile.Endpoint != config["board_url"] {
		return result, queue.ErrConfiguration
	}
	client := *verified
	client.Jar, err = cookiejar.New(nil)
	if err != nil {
		return result, err
	}
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	var source string
	for attempt := 0; attempt < c.Attempts; attempt++ {
		doc, failure := jsonld.FetchDocumentWithClient(ctx, profile.Endpoint, c.Document, &client)
		result.Response = nil
		if doc.Responses > 0 {
			var policy *string
			if doc.TDMPolicy != "" {
				text := doc.TDMPolicy
				policy = &text
			}
			result.Response = &GreenhouseResponse{endpoint: profile.Endpoint, finalURL: doc.FinalURL, status: doc.Status, reserved: doc.ErrorKind == "tdm", policy: policy, reservationSource: doc.TDMSource}
		}
		if doc.ErrorKind == "tdm" {
			return result, failure
		}
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		if failure == nil && doc.Status == 200 && len(doc.Body) > 0 {
			contentType := doc.ContentType
			if c.Encoding != "" {
				contentType = mime.FormatMediaType("text/html", map[string]string{"charset": c.Encoding})
			}
			source = jsonld.DecodeDocument(doc.Body, contentType)
			break
		}
		if failure == nil && doc.Status != 200 && doc.Status != 202 && doc.Status != 401 && doc.Status != 403 && doc.Status != 429 && doc.Status < 500 {
			return result, &DiscoveryError{Kind: "inventory_failed", cause: errors.New("DOM listing HTTP failure")}
		}
		if attempt == c.Attempts-1 {
			return result, &DiscoveryError{Kind: "inventory_failed", cause: errors.New("DOM listing retry exhausted")}
		}
		delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return result, ctx.Err()
		case <-timer.C:
		}
	}
	// Preserve the existing 500,000-codepoint single-page listing preview.
	count := 0
	for offset := range source {
		if count == 500_000 {
			source = source[:offset]
			break
		}
		count++
	}
	classification, err := dom.ClassifyDocument(source, dom.Object{}, profile.Endpoint)
	if err != nil || classification["classification"] == "challenge" {
		return result, &DiscoveryError{Kind: "inventory_failed", cause: errors.New("DOM listing origin challenge")}
	}
	hrefs, err := dom.ListingHrefs(source, c.Selector)
	if err != nil {
		return result, err
	}
	include, err := dom.CompileURLPattern(c.Include)
	if err != nil {
		return result, err
	}
	exclude, err := dom.CompileURLPattern(c.Exclude)
	if err != nil {
		return result, err
	}
	urls := map[string]struct{}{}
	for _, href := range hrefs {
		if ctx.Err() != nil {
			return RichDiscovery{}, ctx.Err()
		}
		absolute, ok := joinPythonURL(profile.Endpoint, href)
		if !ok || !strings.HasPrefix(absolute, "http") {
			continue
		}
		keep := true
		if c.Include != "" {
			keep, err = include.MatchString(absolute)
			if err != nil {
				return RichDiscovery{}, err
			}
		} else if c.Selector == "" {
			p, ok := parsePythonURL(absolute)
			if !ok {
				continue
			}
			text := cases.Fold().String(p.path + "?" + p.query + "#" + p.fragment)
			keep = false
			for _, key := range []string{"job", "career", "position", "posting", "opening", "role", "vacancy", "vacancies", "stellenangebot", "advertisement_display"} {
				if strings.Contains(text, key) {
					keep = true
					break
				}
			}
		}
		if !keep {
			continue
		}
		if c.Exclude != "" {
			reject, err := exclude.MatchString(absolute)
			if err != nil {
				return RichDiscovery{}, err
			}
			if reject {
				continue
			}
		}
		p, ok := parsePythonURL(absolute)
		if !ok {
			continue
		}
		p.fragment = ""
		if strings.TrimRight(p.String(), "/") == strings.TrimRight(profile.Endpoint, "/") {
			continue
		}
		urls[absolute] = struct{}{}
	}
	ordered := make([]string, 0, len(urls))
	for raw := range urls {
		ordered = append(ordered, raw)
	}
	sort.Strings(ordered)
	if len(ordered) > 50_000 {
		result.Truncated = true
		ordered = ordered[:50_000]
	}
	for _, raw := range ordered {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: raw})
	}
	return result, nil
}

// Resolve listing hrefs with the same raw Unicode/escape behavior as Python
// urllib, using the existing URL decomposition used by canonical inventory.
func joinPythonURL(base, reference string) (string, bool) {
	if reference == "" {
		return base, true
	}
	b, ok := parsePythonURL(base)
	if !ok {
		return "", false
	}
	r, ok := parsePythonURL(reference)
	if !ok {
		return "", false
	}
	if r.scheme == "" {
		r.scheme = b.scheme
	}
	if r.scheme != b.scheme {
		return reference, true
	}
	if r.host != "" {
		return r.String(), true
	}
	r.host = b.host
	if r.path == "" && r.params == "" {
		r.path, r.params = b.path, b.params
		if r.query == "" {
			r.query = b.query
		}
		return r.String(), true
	}
	path := r.path
	if r.params != "" {
		path += ";" + r.params
	}
	r.params = ""
	basePath := b.path
	if b.params != "" {
		basePath += ";" + b.params
	}
	var segments []string
	if strings.HasPrefix(path, "/") {
		segments = strings.Split(path, "/")
	} else {
		parts := strings.Split(basePath, "/")
		if parts[len(parts)-1] != "" {
			parts = parts[:len(parts)-1]
		}
		segments = append(parts, strings.Split(path, "/")...)
		filtered := []string{segments[0]}
		for _, part := range segments[1 : len(segments)-1] {
			if part != "" {
				filtered = append(filtered, part)
			}
		}
		segments = append(filtered, segments[len(segments)-1])
	}
	resolved := []string{}
	for _, part := range segments {
		switch part {
		case "..":
			if len(resolved) > 0 {
				resolved = resolved[:len(resolved)-1]
			}
		case ".":
		default:
			resolved = append(resolved, part)
		}
	}
	last := segments[len(segments)-1]
	if last == "." || last == ".." {
		resolved = append(resolved, "")
	}
	r.path = strings.Join(resolved, "/")
	if r.path == "" {
		r.path = "/"
	}
	return r.String(), true
}
