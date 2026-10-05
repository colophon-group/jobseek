package dom

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

type ICIMSRequest struct {
	URL                   string
	JSON, FollowRedirects bool
}
type ICIMSFetch func(context.Context, ICIMSRequest) ([]byte, error)
type ICIMSInventory struct {
	URLs      []string
	Truncated bool
}
type icimsPages struct {
	urls       map[string]bool
	identities map[string]ICIMSIdentity
	truncated  bool
}

func icimsSorted(urls map[string]bool) []string {
	out := make([]string, 0, len(urls))
	for s := range urls {
		out = append(out, s)
	}
	sort.Strings(out)
	return out
}
func icimsID(source string) string { u, _ := url.Parse(source); return strings.Split(u.Path, "/")[2] }
func icimsPagesForHost(ctx context.Context, host string, hosts []string, identities, duplicates bool, fetch ICIMSFetch) (icimsPages, error) {
	out := icimsPages{urls: map[string]bool{}, identities: map[string]ICIMSIdentity{}}
	total := 0
	for index := 0; index < 1000; index++ {
		if index > 0 && index >= total {
			break
		}
		if index > 0 && len(out.urls) >= 50000 {
			out.truncated = true
			break
		}
		if err := ctx.Err(); err != nil {
			return out, err
		}
		endpoint := ICIMSListingURL(host, index)
		raw, err := fetch(ctx, ICIMSRequest{URL: endpoint})
		if err != nil {
			return out, err
		}
		source := string(raw)
		classification, err := ClassifyDocument(source, Object{}, endpoint)
		if err != nil || classification["classification"] == "challenge" || !strings.Contains(strings.ToLower(source), "icims_listingspage") {
			return out, ErrICIMS
		}
		current, advertised := ICIMSPageMetadata(source)
		if current != 0 && current != index+1 {
			return out, ErrICIMS
		}
		if index == 0 {
			total = advertised
			out.truncated = total > 1000
		} else if advertised != total {
			out.truncated = true
		}
		urls, err := ICIMSListingURLs(source, host, hosts)
		if err != nil || index > 0 && len(urls) == 0 {
			return out, ErrICIMS
		}
		for source := range urls {
			if out.urls[source] && !duplicates {
				out.truncated = true
			}
			out.urls[source] = true
		}
		if identities {
			rows, err := ICIMSListingIdentities(source, host, hosts)
			if err != nil || len(rows) != len(urls) {
				return out, ErrICIMS
			}
			for source, row := range rows {
				if !urls[source] {
					return out, ErrICIMS
				}
				if old, ok := out.identities[source]; ok && old != row {
					return out, ErrICIMS
				}
				out.identities[source] = row
			}
		}
		if utf8.RuneCountInString(source) >= 2000000 {
			out.truncated = true
		}
	}
	return out, nil
}

var icimsJibeRedirect = regexp.MustCompile(`(?i)^\s*<script\s+type=["']text/javascript["']>\s*window[.]top[.]location[.]href\s*=\s*["'](https:[^"']+)["'];\s*</script>\s*$`)

func icimsJibeInventory(ctx context.Context, o ICIMSOptions, fetch ICIMSFetch) (ICIMSInventory, error) {
	out := ICIMSInventory{URLs: []string{}}
	raw, err := fetch(ctx, ICIMSRequest{URL: ICIMSListingURL(o.Host, 0)})
	if err != nil {
		return out, err
	}
	match := icimsJibeRedirect.FindStringSubmatch(string(raw))
	if match == nil || strings.ReplaceAll(match[1], `\/`, "/") != o.JibeURL {
		return out, ErrICIMS
	}
	u, _ := url.Parse(o.JibeURL)
	u.Path = "/api/jobs"
	u.RawPath = ""
	seen, urls := map[string]bool{}, map[string]bool{}
	total := -1
	for page := 1; total < 0 || len(seen) < total; page++ {
		if page > 1000 {
			return out, ErrICIMS
		}
		if err := ctx.Err(); err != nil {
			return out, err
		}
		u.RawQuery = "page=" + strconv.Itoa(page) + "&limit=100"
		raw, err := fetch(ctx, ICIMSRequest{URL: u.String(), JSON: true, FollowRedirects: true})
		if err != nil {
			return out, err
		}
		var payload map[string]any
		d := json.NewDecoder(bytes.NewReader(raw))
		d.UseNumber()
		if d.Decode(&payload) != nil || d.Decode(new(any)) != io.EOF {
			return out, ErrICIMS
		}
		count, ok := payload["totalCount"].(json.Number)
		n, err := strconv.Atoi(string(count))
		rows, rowsOK := payload["jobs"].([]any)
		if !ok || err != nil || n < 0 || n > 50000 || !rowsOK || (total >= 0 && n != total) {
			return out, ErrICIMS
		}
		total = n
		if len(rows) == 0 && len(seen) < total {
			return out, ErrICIMS
		}
		for _, v := range rows {
			row, ok := v.(map[string]any)
			if !ok {
				return out, ErrICIMS
			}
			data, ok := row["data"].(map[string]any)
			if !ok {
				return out, ErrICIMS
			}
			slug, ok := data["slug"].(string)
			apply, applyOK := data["apply_url"].(string)
			if !ok || !regexp.MustCompile(`^[\p{Nd}]+$`).MatchString(slug) || !applyOK {
				return out, ErrICIMS
			}
			parsed, err := url.Parse(apply)
			if err != nil {
				return out, ErrICIMS
			}
			host := icimsHost(parsed.Hostname())
			if !hasICIMSHost(o.JibeHosts, host) || parsed.Scheme != "https" || parsed.User != nil || (parsed.Port() != "" && parsed.Port() != "443") || parsed.Path != "/jobs/"+slug+"/login" || parsed.RawQuery != "" || parsed.Fragment != "" || seen[slug] {
				return out, ErrICIMS
			}
			seen[slug] = true
			if host == o.Host {
				urls["https://"+o.Host+"/jobs/"+slug+"/job?in_iframe=1"] = true
			}
		}
	}
	if len(seen) != total {
		return out, ErrICIMS
	}
	out.URLs = icimsSorted(urls)
	return out, nil
}
func DiscoverICIMS(ctx context.Context, o ICIMSOptions, fetch ICIMSFetch) (ICIMSInventory, error) {
	out := ICIMSInventory{URLs: []string{}}
	if fetch == nil || icimsHost(o.Host) != o.Host {
		return out, ErrICIMS
	}
	if o.JibeURL != "" {
		return icimsJibeInventory(ctx, o, fetch)
	}
	result, err := icimsPagesForHost(ctx, o.Host, o.JobHosts, o.PeerHost != "", len(o.JobHosts) > 1, fetch)
	if err != nil {
		return out, err
	}
	if len(o.JobHosts) > 1 {
		ids, children := map[string]bool{}, map[string]bool{}
		for source := range result.urls {
			ids[icimsID(source)] = true
		}
		for _, host := range o.JobHosts {
			if host == o.Host {
				continue
			}
			child, err := icimsPagesForHost(ctx, host, []string{host}, false, false, fetch)
			if err != nil {
				return out, err
			}
			result.truncated = result.truncated || child.truncated
			for source := range child.urls {
				children[icimsID(source)] = true
			}
		}
		if len(ids) != len(result.urls) || len(ids) != len(children) {
			result.truncated = true
		}
		for id := range ids {
			if !children[id] {
				result.truncated = true
			}
		}
	}
	if len(o.IDDedupeHosts) > 0 {
		peers := map[string]bool{}
		for _, host := range o.IDDedupeHosts {
			peer, err := icimsPagesForHost(ctx, host, []string{host}, false, false, fetch)
			if err != nil || peer.truncated {
				return out, ErrICIMS
			}
			for source := range peer.urls {
				peers[icimsID(source)] = true
			}
		}
		for source := range result.urls {
			if peers[icimsID(source)] {
				delete(result.urls, source)
			}
		}
	}
	if o.PeerHost != "" {
		peer, err := icimsPagesForHost(ctx, o.PeerHost, []string{o.PeerHost}, true, false, fetch)
		if err != nil || peer.truncated {
			return out, ErrICIMS
		}
		canonical := func(row ICIMSIdentity) ICIMSIdentity {
			if title, ok := o.TitleAliases[row.Title]; ok {
				row.Title = title
			}
			return row
		}
		unmatched := map[ICIMSIdentity]int{}
		for _, row := range peer.identities {
			unmatched[canonical(row)]++
		}
		for _, source := range icimsSorted(result.urls) {
			key := canonical(result.identities[source])
			if unmatched[key] > 0 {
				delete(result.urls, source)
				unmatched[key]--
			}
		}
	}
	out.URLs, out.Truncated = icimsSorted(result.urls), result.truncated
	return out, nil
}
