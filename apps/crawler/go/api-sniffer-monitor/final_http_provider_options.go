package apisniffer

import (
	"net/url"
	"strconv"
	"strings"
)

type FinalHTTPProviderOptions struct {
	Provider, BoardURL, Origin, Tenant, Kind                                              string
	RecruitTypes                                                                          []int
	ClientID, DaysBack, SnapshotAttempts, SemanticZeroAttempts, PageSize                  int
	Currency, SalaryUnit, Language, APIKey, SegmentID, JobURLTemplate, Locale, CareerPage string
}

func FinalHTTPProviderOptionsFromMetadata(provider, board, raw string) (FinalHTTPProviderOptions, error) {
	o := FinalHTTPProviderOptions{Provider: provider, BoardURL: board}
	if strings.TrimSpace(raw) == "" {
		raw = "{}"
	}
	m, err := DecodeInlineMetadata(raw)
	u, parseErr := url.Parse(board)
	validationURL := board
	if parseErr == nil && provider == "wecruit" && u.Fragment == "/" {
		copyURL := *u
		copyURL.Fragment, copyURL.RawFragment = "", ""
		validationURL = copyURL.String()
	}
	if err != nil || parseErr != nil || !validURL(validationURL) || u.Scheme != "https" || u.User != nil || (u.Fragment != "" && !(provider == "wecruit" && u.Fragment == "/")) || u.Port() != "" && u.Port() != "443" {
		return o, ErrOptions
	}
	o.Origin = "https://" + strings.ToLower(u.Host)
	allowed := map[string]bool{}
	switch provider {
	case "curately", "inploi", "jobconvo":
		if err = groupedHTTPOptions(&o, u, m, allowed); err != nil {
			return o, err
		}
	case "paynet":
		o.Tenant, err = PayNetCompanyFromURL(board)
		if err != nil {
			return o, ErrOptions
		}
	case "nowhiring":
		allowed["slug"] = true
		o.Tenant, err = NowHiringSlugFromURL(board)
		if value, present := m["slug"]; present && detailTruthy(value) {
			text, ok := value.(string)
			if !ok || !nowHiringSlug.MatchString(text) || !strings.EqualFold(u.Hostname(), "nowhiring.com") {
				return o, ErrOptions
			}
			o.Tenant, err = strings.ToLower(text), nil
		}
		if err != nil {
			return o, ErrOptions
		}
	case "fenbi":
		allowed["kind"] = true
		o.Kind, _ = m["kind"].(string)
		path := "/page/joinus"
		if o.Kind == "parttime" {
			path += "/parttime"
		}
		if u.Hostname() != "www.fenbi.com" || u.Path != path || u.RawQuery != "" || o.Kind != "fulltime" && o.Kind != "parttime" {
			return o, ErrOptions
		}
		o.Tenant = "careers"
	case "wecruit":
		allowed["suite_key"], allowed["api_origin"], allowed["recruit_types"] = true, true, true
		suite, ok := m["suite_key"].(string)
		origin, originOK := m["api_origin"].(string)
		apiURL, apiErr := url.Parse(origin)
		if !ok || !regexpWecruitSuite(suite) || !originOK || apiErr != nil || !validURL(origin) || apiURL.Scheme != "https" || apiURL.User != nil || apiURL.Path != "" || apiURL.RawQuery != "" || apiURL.Fragment != "" || apiURL.Port() != "" && apiURL.Port() != "443" || origin != "https://"+strings.ToLower(apiURL.Host) {
			return o, ErrOptions
		}
		// Current boards explicitly supply the verified public conversation scope.
		// Tenant discovery from an arbitrary landing page remains outside admission.
		if apiURL.Hostname() != "wecruit.hotjob.cn" && !strings.EqualFold(apiURL.Hostname(), u.Hostname()) {
			return o, ErrOptions
		}
		o.Origin, o.Tenant = origin, strings.ToLower(suite)
		o.RecruitTypes = []int{1, 2, 12, 13}
		if value, present := m["recruit_types"]; present && value != nil {
			values, ok := value.([]any)
			if !ok || len(values) == 0 || len(values) > 4 {
				return o, ErrOptions
			}
			o.RecruitTypes = []int{}
			seen := map[int]bool{}
			for _, value := range values {
				lane, err := smallInt(value, false)
				if err != nil || lane != 1 && lane != 2 && lane != 12 && lane != 13 || seen[lane] {
					return o, ErrOptions
				}
				seen[lane] = true
				o.RecruitTypes = append(o.RecruitTypes, lane)
			}
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

func (o FinalHTTPProviderOptions) IdentityMatches(source, identity string) bool {
	if o.Provider == "curately" || o.Provider == "inploi" || o.Provider == "jobconvo" {
		return identity == "" && o.groupedJobMatches(source)
	}
	if !explicitNextdataIdentity.MatchString(identity) {
		return false
	}
	parts := strings.SplitN(identity, ":", 3)
	if len(parts) != 3 || parts[0] != o.Provider {
		return false
	}
	switch o.Provider {
	case "paynet":
		return parts[1] == strings.ToLower(o.Tenant) && payNetUUID.MatchString(parts[2]) && source == "https://www.pay-netonline.com/PayNet/Applicant/Posting.aspx?JobPostingID="+parts[2]
	case "nowhiring":
		return smallUnsigned.MatchString(parts[1]) && nowHiringID.MatchString(parts[2]) && source == "https://nowhiring.com/"+o.Tenant+"/job-details/"+parts[2]
	case "fenbi":
		if parts[1] != "careers" || !strings.HasPrefix(parts[2], o.Kind+"-") {
			return false
		}
		id := strings.TrimPrefix(parts[2], o.Kind+"-")
		base, err := url.Parse(o.BoardURL)
		return err == nil && smallUnsigned.MatchString(id) && strings.TrimLeft(id, "0") != "" && source == base.ResolveReference(&url.URL{Path: "/page/joinusdetail/" + o.Kind + "/" + id}).String()
	case "wecruit":
		if parts[1] != o.Tenant || !regexpWecruitSuite(parts[2]) {
			return false
		}
		u, err := url.Parse(source)
		base, baseErr := url.Parse(o.Origin)
		if err != nil || baseErr != nil || u.Scheme != base.Scheme || u.Host != base.Host || u.User != nil || u.Fragment != "" || u.Path != "/SU"+o.Tenant+"/pb/posDetail.html" {
			return false
		}
		query, err := url.ParseQuery(u.RawQuery)
		if err != nil || len(query) != 2 || len(query["postId"]) != 1 || len(query["postType"]) != 1 || query.Get("postId") != parts[2] {
			return false
		}
		for _, lane := range o.RecruitTypes {
			text := query.Get("postType")
			if text == strconv.Itoa(lane) || text == strconv.Itoa(lane)+".0" || lane == 1 && text == "True" {
				return true
			}
		}
	}
	return false
}

func (o FinalHTTPProviderOptions) Profile() string {
	if o.Provider == "jobconvo" {
		return "jobconvo.listing-urls/v1"
	}
	return o.Provider + ".public-items/v1"
}

func (o FinalHTTPProviderOptions) ListingURL() string {
	switch o.Provider {
	case "curately":
		if o.ClientID > 0 {
			return CuratelySearchURL
		}
		return CuratelyAPIBase + "/getByShortName/" + o.Tenant
	case "inploi":
		if o.APIKey != "" && o.SegmentID != "" {
			return InploiSearchRequest(o.APIKey, o.SegmentID, 1, o.PageSize).URL
		}
		return o.BoardURL
	case "jobconvo":
		return o.BoardURL
	case "paynet":
		return PayNetAPIURL + "?company_id=" + url.QueryEscape(o.Tenant)
	case "nowhiring":
		return "https://nowhiring.com/api/career-live-sites/" + url.PathEscape("nowhiring.com/"+o.Tenant)
	case "fenbi":
		return o.BoardURL
	case "wecruit":
		return o.Origin + "/wecruit/positionInfo/listPosition/SU" + o.Tenant
	}
	return ""
}

func (o FinalHTTPProviderOptions) ResourceMatches(source string) bool {
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" {
		return false
	}
	switch o.Provider {
	case "curately":
		return source == CuratelySearchURL || source == CuratelyAPIBase+"/getByShortName/"+o.Tenant
	case "inploi", "jobconvo":
		return o.groupedResourceMatches(source)
	case "paynet":
		return source == o.ListingURL()
	case "nowhiring":
		if source == o.ListingURL() || source == "https://nowhiring.com/api/jobs/search" {
			return true
		}
		return u.Host == "nowhiring.com" && u.RawQuery == "" && strings.HasPrefix(u.Path, "/api/jobs/") && nowHiringID.MatchString(strings.TrimPrefix(u.Path, "/api/jobs/"))
	case "fenbi":
		return u.Hostname() == "www.fenbi.com" || u.Hostname() == "nodestatic.fbstatic.cn" && fenbiBundlePath.MatchString(u.Path) && u.RawQuery == ""
	case "wecruit":
		return source == o.ListingURL() || source == o.Origin+"/wecruit/positionInfo/listPositionDetail/SU"+o.Tenant
	}
	return false
}
