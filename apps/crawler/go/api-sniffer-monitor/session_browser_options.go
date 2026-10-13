package apisniffer

import (
	"net/url"
	"strings"
)

// Session controls are fixed in the renderer. A legacy transport or browser
// feature that has no proven replacement refuses admission instead of vanishing.
func SessionBrowserOptions(provider, board, raw string) (BrowserReplayOptions, string, error) {
	o := BrowserReplayOptions{Wait: "domcontentloaded", TimeoutMS: 60000, ResponseBodyLimit: 16 << 20}
	m, err := DecodeInlineMetadata(raw)
	if err != nil {
		return o, "", err
	}
	for key, value := range m {
		switch key {
		case "scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "config_fingerprint", "_confirmed_drop_candidate", "_last_discovered_count", "_zero_confirmed", "_zero_confirm_count":
		case "wait":
			wait, ok := value.(string)
			if !ok || wait != "domcontentloaded" && wait != "load" {
				return o, "", ErrOptions
			}
			o.Wait = wait
		case "timeout":
			n, ok := integer(value)
			if !ok || n < 1 || n > 120000 {
				return o, "", ErrOptions
			}
			o.TimeoutMS = uint64(n)
		case "partner_id", "site_id":
			b, e := BrassRingBoardFromURL(board)
			identity, ok := value.(string)
			if provider != "brassring" || e != nil || !ok || key == "partner_id" && identity != b.PartnerID || key == "site_id" && identity != b.SiteID {
				return o, "", ErrOptions
			}
		case "proxy", "stealth", "persistent_context":
			if value != false {
				return o, "", ErrOptions
			}
		case "page_wait_ms", "max_pages", "snapshot_attempts", "page_change_timeout_ms":
			if provider != "njoyn" {
				return o, "", ErrOptions
			}
			if _, ok := integer(value); !ok {
				return o, "", ErrOptions
			}
		default:
			return o, "", ErrOptions
		}
	}
	if provider == "brassring" {
		if _, err := BrassRingBoardFromURL(board); err != nil {
			return o, "", err
		}
		u, _ := url.Parse(board)
		if u.Scheme != "https" || u.User != nil || u.Port() != "" && u.Port() != "443" || !strings.HasSuffix(strings.ToLower(u.Hostname()), ".brassring.com") {
			return o, "", ErrOptions
		}
		u.Path, u.RawPath, u.RawQuery, u.Fragment = "/TGnewUI/Search/Ajax/MatchedJobs", "", "", ""
		o.Inventory = Options{Endpoint: u.String(), Method: "POST"}
		return o, board, nil
	}
	if provider == "njoyn" {
		if _, err := NjoynBoardFromURL(board); err != nil {
			return o, "", err
		}
		if _, _, _, _, err := NjoynSessionLimits(raw); err != nil {
			return o, "", err
		}
		o.Inventory = Options{Endpoint: board, Method: "POST"}
		return o, board, nil
	}
	return o, "", ErrOptions
}

func NjoynSessionLimits(raw string) (maxPages, attempts, waitMS, navigationMS int, err error) {
	m, err := DecodeInlineMetadata(raw)
	if err != nil {
		return 0, 0, 0, 0, err
	}
	if n, ok := integer(m["max_pages"]); ok && n < 1 {
		return 0, 0, 0, 0, ErrOptions
	}
	get := func(key string, fallback, low, high int) int {
		if n, ok := integer(m[key]); ok {
			return min(high, max(low, n))
		}
		return fallback
	}
	return get("max_pages", 200, 1, 200), get("snapshot_attempts", 2, 1, 3), get("page_wait_ms", 4000, 0, 60000), get("page_change_timeout_ms", 15000, 1000, 60000), nil
}

func BrassRingAjaxMatches(board, resource, method string) bool {
	b, err := url.Parse(board)
	u, e := url.Parse(resource)
	if err != nil || e != nil || method != "POST" || u.User != nil || u.Scheme != "https" || !strings.EqualFold(b.Host, u.Host) {
		return false
	}
	path := strings.ToLower(u.Path)
	return path == "/tgnewui/search/ajax/matchedjobs" || path == "/tgnewui/search/ajax/processsortandshowmorejobs"
}

func BrassRingDetailMatches(board, resource string) bool {
	b, err := BrassRingBoardFromURL(board)
	u, e := url.Parse(resource)
	origin, f := url.Parse(board)
	posting, g := BrassRingBoardFromURL(resource)
	return err == nil && e == nil && f == nil && g == nil && u.User == nil && u.Scheme == "https" && strings.EqualFold(u.Host, origin.Host) && posting.PartnerID == b.PartnerID && posting.SiteID == b.SiteID && brassRingDigits.MatchString(u.Query().Get("jobid"))
}

func SessionListingMatches(provider, board, resource string) bool {
	if provider == "njoyn" {
		b, e := NjoynBoardFromURL(board)
		r, f := NjoynBoardFromURL(resource)
		return e == nil && f == nil && b.Host == r.Host && b.Tenant == r.Tenant
	}
	if provider == "brassring" {
		b, e := BrassRingBoardFromURL(board)
		r, f := BrassRingBoardFromURL(resource)
		u, g := url.Parse(resource)
		origin, h := url.Parse(board)
		return e == nil && f == nil && g == nil && h == nil && u.User == nil && u.Scheme == "https" && strings.EqualFold(u.Host, origin.Host) && b.PartnerID == r.PartnerID && b.SiteID == r.SiteID && u.Query().Get("jobid") == ""
	}
	return false
}
