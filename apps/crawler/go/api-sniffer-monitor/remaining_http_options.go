package apisniffer

import (
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

const JobDivaAPI = "https://ws.jobdiva.com/candPortal/rest"
const HeadHunterAPI = "https://api.hh.ru/vacancies"

var johdiCompanyKey = regexp.MustCompile(`^[A-Za-z0-9_=-]+$`)
var johdiFlow = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
var johdiLocale = regexp.MustCompile(`^[A-Za-z]{2}(?:-[A-Za-z]{2})?$`)
var johdiOfferID = regexp.MustCompile(`^[1-9][0-9]{0,18}$`)
var jobDivaTenant = regexp.MustCompile(`^[A-Za-z0-9_-]{16,128}$`)
var headHunterEmployerPath = regexp.MustCompile(`(?i)^/employer/([0-9]+)/?$`)
var headHunterHosts = map[string]bool{"hh.ru": true, "rabota.by": true, "hh1.az": true, "hh.uz": true, "hh.kz": true, "headhunter.ge": true, "headhunter.kg": true}

type RemainingHTTPOptions struct {
	Provider, BoardURL, Tenant, Host, CompanyKey, Flow, Locale string
	Proxy                                                      bool
}

func RemainingHTTPOptionsFromMetadata(provider, board, raw string) (RemainingHTTPOptions, error) {
	o := RemainingHTTPOptions{Provider: provider, BoardURL: board}
	if strings.TrimSpace(raw) == "" {
		raw = "{}"
	}
	m, e := DecodeInlineMetadata(raw)
	u, ue := url.Parse(board)
	if e != nil || ue != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Port() != "" && u.Port() != "443" {
		return o, ErrOptions
	}
	allowed := map[string]bool{}
	switch provider {
	case "johdi":
		if u.Fragment != "" {
			return o, ErrOptions
		}
		o.CompanyKey, _ = m["company_key"].(string)
		o.Flow, _ = m["flow"].(string)
		o.Locale, _ = m["locale"].(string)
		if len(o.CompanyKey) < 16 || len(o.CompanyKey) > 4096 || !johdiCompanyKey.MatchString(o.CompanyKey) || !johdiFlow.MatchString(o.Flow) || !johdiLocale.MatchString(o.Locale) {
			return o, ErrOptions
		}
		allowed["company_key"], allowed["flow"], allowed["locale"] = true, true, true
	case "jobdiva":
		if u.Hostname() != "www1.jobdiva.com" && u.Hostname() != "www2.jobdiva.com" && u.Hostname() != "jobdiva.com" || strings.TrimRight(u.Path, "/") != "/portal" || u.Fragment != "" && u.Fragment != "/" {
			return o, ErrOptions
		}
		o.Tenant, _ = m["token"].(string)
		if !jobDivaTenant.MatchString(o.Tenant) {
			o.Tenant = u.Query().Get("a")
		}
		if !jobDivaTenant.MatchString(o.Tenant) || u.Query().Get("a") != "" && u.Query().Get("a") != o.Tenant {
			return o, ErrOptions
		}
		allowed["token"] = true
	case "headhunter":
		if u.Port() != "" || u.Fragment != "" {
			return o, ErrOptions
		}
		host := strings.TrimPrefix(strings.ToLower(u.Hostname()), "www.")
		if host == "api.hh.ru" && strings.TrimRight(u.Path, "/") == "/vacancies" {
			o.Tenant = u.Query().Get("employer_id")
			host = u.Query().Get("host")
			if host == "" {
				host = "hh.ru"
			}
		} else if match := headHunterEmployerPath.FindStringSubmatch(u.Path); len(match) == 2 && headHunterHosts[host] {
			o.Tenant = match[1]
		} else {
			return o, ErrOptions
		}
		if value := m["employer_id"]; detailTruthy(value) {
			id := remainingID(value)
			if id == "" || id != o.Tenant {
				return o, ErrOptions
			}
		}
		o.Host = host
		if value := m["host"]; detailTruthy(value) {
			configured, ok := value.(string)
			if !ok {
				return o, ErrOptions
			}
			o.Host = configured
		}
		if !smallUnsigned.MatchString(o.Tenant) || !headHunterHosts[o.Host] {
			return o, ErrOptions
		}
		if value, present := m["proxy"]; present {
			var ok bool
			o.Proxy, ok = value.(bool)
			if !ok {
				return o, ErrOptions
			}
		}
		allowed["employer_id"], allowed["host"], allowed["proxy"] = true, true, true
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

func (o RemainingHTTPOptions) Profile() string {
	if o.Provider == "headhunter" {
		if o.Proxy {
			return "headhunter.proxy-summary-items/v1"
		}
		return "headhunter.summary-items/v1"
	}
	return o.Provider + ".listing-urls/v1"
}
func (o RemainingHTTPOptions) ListingURL() string {
	switch o.Provider {
	case "johdi":
		return o.BoardURL
	case "jobdiva":
		return JobDivaAPI + "/auth/a"
	case "headhunter":
		return o.HeadHunterRequest(0).URL
	}
	return ""
}
func (o RemainingHTTPOptions) JohdiListURL() string {
	return "https://ats.johdisuite.ch/api/company/" + o.CompanyKey + "/publicationFlows/" + o.Flow + "/offers/" + o.Locale
}
func (o RemainingHTTPOptions) HeadHunterRequest(page int) Request {
	q := url.Values{"employer_id": {o.Tenant}, "page": {strconv.Itoa(page)}, "per_page": {"100"}, "host": {o.Host}}
	return Request{Method: "GET", URL: HeadHunterAPI + "?" + q.Encode(), Headers: headHunterHeaders(false)}
}
func (o RemainingHTTPOptions) HeadHunterPublicURL() string {
	return "https://" + o.Host + "/search/vacancy?employer_id=" + url.QueryEscape(o.Tenant)
}
func (o RemainingHTTPOptions) ResourceMatches(source string) bool {
	if o.Provider == "johdi" {
		return source == o.BoardURL || source == o.JohdiListURL()
	}
	if o.Provider == "jobdiva" {
		if source == o.ListingURL() || source == JobDivaAPI+"/job/searchjobsportal" {
			return true
		}
		u, e := url.Parse(source)
		if e != nil || u.Scheme != "https" || u.Host != "ws.jobdiva.com" || u.User != nil || u.Fragment != "" || u.Path != "/candPortal/rest/job/getmore" {
			return false
		}
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil || len(q) != 4 || q.Get("to") != "0" || q.Get("portaltype") != "1" || q.Get("count") != "200" && q.Get("count") != "201" {
			return false
		}
		for _, v := range q {
			if len(v) != 1 {
				return false
			}
		}
		n, e := strconv.Atoi(q.Get("from"))
		return e == nil && n >= 201 && n <= 49801 && (n-1)%200 == 0
	}
	if o.Provider == "headhunter" {
		if source == o.HeadHunterPublicURL() {
			return true
		}
		for page := 0; page < 20; page++ {
			if source == o.HeadHunterRequest(page).URL {
				return true
			}
		}
	}
	return false
}
func (o RemainingHTTPOptions) JobMatches(source string) bool {
	switch o.Provider {
	case "johdi":
		prefix := strings.TrimRight(o.BoardURL, "/") + "#/offer/"
		if !strings.HasPrefix(source, prefix) || !strings.HasSuffix(source, "/job") {
			return false
		}
		return johdiOfferID.MatchString(strings.TrimSuffix(strings.TrimPrefix(source, prefix), "/job"))
	case "jobdiva":
		prefix := "https://www2.jobdiva.com/portal/?a=" + url.QueryEscape(o.Tenant) + "&compid=0#/jobs/"
		id := strings.TrimPrefix(source, prefix)
		return id != source && johdiOfferID.MatchString(id)
	case "headhunter":
		prefix := "https://" + o.Host + "/vacancy/"
		id := strings.TrimPrefix(source, prefix)
		return id != source && smallUnsigned.MatchString(id)
	}
	return false
}
