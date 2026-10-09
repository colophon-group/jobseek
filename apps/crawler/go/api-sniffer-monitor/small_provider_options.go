package apisniffer

import (
	"net/url"
	"regexp"
	"strings"
)

type SmallProviderOptions struct {
	Provider, BoardURL, Origin, Tenant string
	Proxy                              bool
	PublicKey, Currency                string
	CTMID                              int64
}

var smallTenant = regexp.MustCompile(`^[a-z0-9-]+$`)
var jobbankToken = regexp.MustCompile(`(?i)^[a-z0-9]{5,16}$`)

func SmallProviderOptionsFromMetadata(provider, board, raw string) (SmallProviderOptions, error) {
	o := SmallProviderOptions{Provider: provider, BoardURL: board}
	if strings.TrimSpace(raw) == "" {
		raw = "{}"
	}
	m, err := DecodeInlineMetadata(raw)
	u, urlErr := url.Parse(board)
	if err != nil || urlErr != nil || !validURL(board) || u.Scheme != "https" || u.User != nil || u.Port() != "" && u.Port() != "443" || u.Fragment != "" || u.RawQuery != "" {
		return o, ErrOptions
	}
	host := strings.ToLower(u.Hostname())
	o.Origin = "https://" + host
	allowed := map[string]bool{}
	switch provider {
	case "jarvi":
		allowed["public_api_key"], allowed["currency"] = true, true
		var ok bool
		o.PublicKey, ok = m["public_api_key"].(string)
		if !ok || o.PublicKey == "" || len(o.PublicKey) > 4096 || strings.ContainsAny(o.PublicKey, "\r\n\x00") {
			return o, ErrOptions
		}
		if value, present := m["currency"]; present {
			o.Currency, ok = value.(string)
			if !ok {
				return o, ErrOptions
			}
		}
	case "job51":
		if _, err := Job51BoardOrigin(board); err != nil {
			return o, ErrOptions
		}
		allowed["ctmid"] = true
		o.CTMID, err = job51Int(m["ctmid"])
		if err != nil || o.CTMID < 1 || o.CTMID > 999_999_999_999 {
			return o, ErrOptions
		}
	case "cnstaff":
		if !strings.HasSuffix(host, ".cnstaff.com") || host == "cnstaff.com" || strings.TrimRight(u.Path, "/") != "/recruit" {
			return o, ErrOptions
		}
		allowed["origin"] = true
		if v := m["origin"]; v != nil && v != o.Origin {
			return o, ErrOptions
		}
	case "jobbank104":
		if host != "www.104.com.tw" {
			return o, ErrOptions
		}
		parts := strings.FieldsFunc(u.Path, func(r rune) bool { return r == '/' })
		if len(parts) == 2 && strings.EqualFold(parts[0], "company") && jobbankToken.MatchString(parts[1]) {
			o.Tenant = strings.ToLower(parts[1])
		}
		allowed["token"], allowed["proxy"] = true, true
		if v, ok := m["token"].(string); ok && jobbankToken.MatchString(strings.TrimSpace(v)) {
			o.Tenant = strings.ToLower(strings.TrimSpace(v))
		}
		if o.Tenant == "" {
			return o, ErrOptions
		}
		if v := m["proxy"]; v != nil {
			b, ok := v.(bool)
			if !ok {
				return o, ErrOptions
			}
			o.Proxy = b
		}
	case "seamlesshiring":
		if !strings.HasSuffix(host, ".seamlesshiring.com") {
			return o, ErrOptions
		}
		o.Tenant = strings.TrimSuffix(host, ".seamlesshiring.com")
		allowed["tenant"] = true
		if v := m["tenant"]; detailTruthy(v) {
			var ok bool
			o.Tenant, ok = v.(string)
			if !ok {
				return o, ErrOptions
			}
		}
		if !smallTenant.MatchString(o.Tenant) || o.Tenant != strings.TrimSuffix(host, ".seamlesshiring.com") {
			return o, ErrOptions
		}
	default:
		return o, ErrOptions
	}
	for key := range m {
		switch key {
		case "scraper_type", "scraper_config", "suspect_streak", "recent_discovered_counts", "_confirmed_drop_candidate", "_monitor_config_fingerprint", "delist_threshold", "drop_threshold", "blast_radius_floor":
		default:
			if !allowed[key] {
				return o, ErrOptions
			}
		}
	}
	return o, nil
}

func (o SmallProviderOptions) Profile() string {
	switch o.Provider {
	case "jarvi":
		return "jarvi.public-items/v1"
	case "job51":
		return "job51.public-items/v1"
	case "cnstaff":
		return "cnstaff.public-items/v1"
	case "jobbank104":
		if o.Proxy {
			return "jobbank104.proxy-public-items/v1"
		}
		return "jobbank104.public-items/v1"
	case "seamlesshiring":
		return "seamlesshiring.public-items/v1"
	}
	return ""
}

func (o SmallProviderOptions) ListingURL() string {
	switch o.Provider {
	case "jarvi":
		return JarviOffersURL
	case "job51":
		request, err := Job51ListRequest(o.CTMID, 1)
		if err != nil {
			return ""
		}
		return request.URL
	case "cnstaff":
		return o.Origin + "/recruit"
	case "jobbank104":
		return "https://www.104.com.tw/api/companies/" + o.Tenant + "/jobs"
	case "seamlesshiring":
		return o.Origin + "/v2/jobs/job-list"
	}
	return ""
}

func (o SmallProviderOptions) ResourceMatches(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.User != nil || u.Fragment != "" || u.Scheme != "https" || u.Port() != "" && u.Port() != "443" {
		return false
	}
	if o.Provider == "jarvi" {
		return raw == JarviOffersURL
	}
	if o.Provider == "job51" {
		query, err := url.ParseQuery(u.RawQuery)
		if err != nil || len(query) != 3 || query.Get("key") != "1" {
			return false
		}
		for _, values := range query {
			if len(values) != 1 {
				return false
			}
		}
		d, err := Decode([]byte(query.Get("params")))
		if err != nil {
			return false
		}
		params, ok := d.Value.(map[string]any)
		if !ok {
			return false
		}
		var request Request
		switch u.Path {
		case "/job_list.php":
			ctmid, e := job51Int(params["ctmid"])
			if e != nil || ctmid != o.CTMID {
				return false
			}
			page, e := job51Int(params["pagenum"])
			if e != nil {
				return false
			}
			request, err = Job51ListRequest(ctmid, int(page))
		case "/job_detail.php":
			id, ok := params["jobid"].(string)
			if !ok {
				return false
			}
			request, err = Job51DetailRequest(id)
		default:
			return false
		}
		return err == nil && request.URL == raw
	}
	u.RawQuery = ""
	return u.String() == o.ListingURL()
}
