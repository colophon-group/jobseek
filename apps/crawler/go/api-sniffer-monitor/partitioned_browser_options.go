package apisniffer

import (
	"encoding/json"
	"net/url"
	"strings"
)

// These factories bind the paired collectors to their configured public board.
// A factory alone grants no renderer or queue ownership.
func AccentureBrowserOptions(board, raw string) (AccentureOptions, BrowserReplayOptions, error) {
	// The configured US board's original 500-row first page is 17,246,836
	// bytes. This fixed provider bound is not configurable through metadata.
	o := BrowserReplayOptions{Wait: "networkidle", WaitFallback: "domcontentloaded", TransportRetries: 1, TimeoutMS: 30000, ResponseBodyLimit: 32 << 20}
	m, e := partitionedBrowserMetadata(raw)
	if e != nil {
		return AccentureOptions{}, o, e
	}
	b, _ := json.Marshal(m)
	a, e := AccentureOptionsFromMetadata(board, string(b))
	if e != nil {
		return a, o, e
	}
	if a.Endpoint == AccentureFindJobs {
		r := a.FindJobsRequest(0, nil)
		o.Inventory = Options{Endpoint: r.URL, Method: r.Method, Body: r.Body, Headers: r.Headers}
	} else {
		// The jobsearch variant needs the page's own captured body. Its factory
		// deliberately does not manufacture a findjobs multipart request.
		o.Inventory = Options{Endpoint: "https://www.accenture.com/api/accenture/" + AccentureJobSearch, Method: "POST"}
	}
	return a, o, nil
}

func BrassRingBrowserOptions(board, raw string) (BrassRingBoard, BrowserReplayOptions, error) {
	o := BrowserReplayOptions{Wait: "domcontentloaded", WaitFallback: "domcontentloaded", TransportRetries: 1, TimeoutMS: 60000, ResponseBodyLimit: 16 << 20}
	b, e := BrassRingBoardFromURL(board)
	if e != nil {
		return b, o, e
	}
	m, e := partitionedBrowserMetadata(raw)
	if e != nil {
		return b, o, e
	}
	for key, v := range m {
		switch key {
		case "partner_id", "site_id":
			s, ok := v.(string)
			if !ok || key == "partner_id" && s != b.PartnerID || key == "site_id" && s != b.SiteID {
				return b, o, ErrOptions
			}
		case "wait":
			s, ok := v.(string)
			if !ok || s != "commit" && s != "domcontentloaded" && s != "load" && s != "networkidle" {
				return b, o, ErrOptions
			}
			o.Wait = s
		case "timeout":
			n, ok := integer(v)
			if !ok || n < 1 || n > 120000 {
				return b, o, ErrOptions
			}
			o.TimeoutMS = uint64(n)
		case "wait_fallback":
			if v == nil {
				o.WaitFallback = ""
				continue
			}
			s, ok := v.(string)
			if !ok || s != "commit" && s != "domcontentloaded" && s != "load" && s != "networkidle" {
				return b, o, ErrOptions
			}
			o.WaitFallback = s
		default:
			return b, o, ErrOptions
		}
	}
	u, _ := url.Parse(board)
	prefix := u.Path[:strings.Index(strings.ToLower(u.Path), "/search/")]
	// Public TGnewUI board links use several capitalizations. The actual
	// provider AJAX application is TgNewUI, including on the configured ADM
	// boards; matching the link's spelling misses every captured response.
	prefix = prefix[:strings.LastIndex(prefix, "/")+1] + "TgNewUI"
	u.Path, u.RawPath, u.RawQuery, u.Fragment = prefix+"/Search/Ajax/MatchedJobs", "", "", ""
	o.Inventory = Options{Endpoint: u.String(), Method: "POST"}
	return b, o, nil
}

func partitionedBrowserMetadata(raw string) (map[string]any, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil {
		return nil, e
	}
	for _, key := range []string{"scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count"} {
		delete(m, key)
	}
	return m, nil
}

// Public detail hydration remains on the board's origin and numeric tenant
// tuple; captured cookies/headers and arbitrary links grant no HTTP authority.
func BrassRingDetailResourceMatches(board, resource string) bool {
	expected, err := BrassRingBoardFromURL(board)
	actual, parseErr := BrassRingBoardFromURL(resource)
	base, baseErr := url.Parse(board)
	target, targetErr := url.Parse(resource)
	if err != nil || parseErr != nil || baseErr != nil || targetErr != nil || expected != actual || base.Scheme != target.Scheme || base.Host != target.Host || target.User != nil || target.Fragment != "" {
		return false
	}
	return brassRingDigits.MatchString(target.Query().Get("jobid")) && strings.EqualFold(target.Query().Get("PageType"), "JobDetails")
}
