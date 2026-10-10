package apisniffer

import (
	"net/url"
	"regexp"
	"strings"
)

var johdiOfferFragment = regexp.MustCompile(`^/offer/([0-9]+)/[A-Za-z0-9_-]+/?$`)
var headHunterVacancyPath = regexp.MustCompile(`(?i)^/vacancy/([0-9]+)/?$`)

func JohdiOfferFragment(fragment string) bool {
	match := johdiOfferFragment.FindStringSubmatch(fragment)
	return len(match) == 2 && johdiOfferID.MatchString(match[1])
}

type RemainingHTTPDetailOptions struct {
	Provider, SourceURL, Endpoint, ID, Host, CompanyKey, Flow, Locale string
	Proxy                                                             bool
}

func RemainingHTTPDetailOptionsFromConfig(provider, board, source, raw string) (RemainingHTTPDetailOptions, error) {
	o := RemainingHTTPDetailOptions{Provider: provider, SourceURL: source}
	if strings.TrimSpace(raw) == "" {
		raw = "{}"
	}
	m, e := DecodeInlineMetadata(raw)
	u, ue := url.Parse(source)
	if e != nil || ue != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Port() != "" && u.Port() != "443" || len(source) > 8192 || strings.ContainsAny(source, "\x00\r\n") {
		return o, ErrOptions
	}
	allowed := map[string]bool{"enrich": true}
	switch provider {
	case "johdi":
		base := *u
		base.Fragment, base.RawFragment = "", ""
		match := johdiOfferFragment.FindStringSubmatch(u.Fragment)
		if strings.TrimRight(base.String(), "/") != strings.TrimRight(board, "/") || len(match) != 2 {
			return o, ErrOptions
		}
		o.ID = match[1]
		if !johdiOfferID.MatchString(o.ID) {
			return o, ErrOptions
		}
		o.CompanyKey, _ = m["company_key"].(string)
		o.Flow, _ = m["flow"].(string)
		o.Locale, _ = m["locale"].(string)
		if len(o.CompanyKey) < 16 || len(o.CompanyKey) > 4096 || !johdiCompanyKey.MatchString(o.CompanyKey) || !johdiFlow.MatchString(o.Flow) || !johdiLocale.MatchString(o.Locale) {
			return o, ErrOptions
		}
		o.Endpoint = "https://ats.johdisuite.ch/api/company/" + o.CompanyKey + "/publicationFlows/" + o.Flow + "/offer/" + o.ID + "/" + o.Locale
		allowed["company_key"], allowed["flow"], allowed["locale"] = true, true, true
	case "headhunter":
		o.Host = strings.TrimPrefix(strings.ToLower(u.Hostname()), "www.")
		match := headHunterVacancyPath.FindStringSubmatch(u.Path)
		if !headHunterHosts[o.Host] || u.Port() != "" || len(match) != 2 {
			return o, ErrOptions
		}
		o.ID = match[1]
		if value, present := m["proxy"]; present {
			var ok bool
			o.Proxy, ok = value.(bool)
			if !ok {
				return o, ErrOptions
			}
		}
		o.Endpoint = HeadHunterAPI + "/" + o.ID + "?host=" + url.QueryEscape(o.Host)
		allowed["proxy"] = true
	default:
		return o, ErrOptions
	}
	for key := range m {
		if !allowed[key] {
			return o, ErrOptions
		}
	}
	return o, nil
}

func JohdiDetailFields(row map[string]any, id, locale string) (map[string]any, error) {
	if remainingPositiveID(row["id"]) != id {
		return nil, ErrInventory
	}
	title, ok := row["title"].(string)
	if !ok || strings.TrimSpace(title) == "" {
		return nil, ErrInventory
	}
	parts := []string{}
	for _, key := range []string{"introduction", "description"} {
		if text, ok := row[key].(string); ok && strings.TrimSpace(text) != "" {
			parts = append(parts, strings.TrimSpace(text))
		}
	}
	if len(parts) == 0 {
		return nil, ErrInventory
	}
	fields := map[string]any{"title": strings.TrimSpace(title), "description": strings.Join(parts, "\n"), "language": strings.ToLower(strings.SplitN(locale, "-", 2)[0])}
	locations := []string{}
	seen := map[string]bool{}
	for _, key := range []string{"work_place", "city", "canton", "country_code"} {
		text, ok := row[key].(string)
		if !ok {
			continue
		}
		text = strings.TrimSpace(text)
		if key == "country_code" {
			text = strings.ToUpper(text)
		}
		if text != "" && !seen[text] {
			locations = append(locations, text)
			seen[text] = true
		}
	}
	if len(locations) > 0 {
		fields["locations"] = []string{strings.Join(locations, ", ")}
	}
	for _, pair := range [][2]string{{"contract_type", "employment_type"}, {"publication_date", "date_posted"}} {
		if text, ok := row[pair[0]].(string); ok {
			fields[pair[1]] = strings.TrimSpace(text)
		}
	}
	metadata := map[string]any{"id": id}
	for _, pair := range [][2]string{{"ref", "reference"}, {"subtitle", "subtitle"}, {"sector", "sector"}, {"activity_from", "activity_from"}, {"activity_to", "activity_to"}, {"expiration_date", "expiration_date"}} {
		if value := row[pair[0]]; value != nil && value != "" {
			metadata[pair[1]] = value
		}
	}
	apply := row["apply_link"]
	if !detailTruthy(apply) {
		apply = row["postulation_url"]
	}
	if text, ok := apply.(string); ok && text != "" {
		metadata["apply_url"] = text
	}
	fields["metadata"] = metadata
	return fields, nil
}

func HeadHunterDetailFields(row map[string]any, id, host string) (map[string]any, error) {
	if remainingID(row["id"]) != id {
		return nil, ErrInventory
	}
	employer, _ := row["employer"].(map[string]any)
	fields, e := HeadHunterFields(row, remainingID(employer["id"]), host)
	if e != nil {
		return nil, e
	}
	delete(fields, "url")
	return fields, nil
}

func HeadHunterDetailHeaders(public bool) map[string][]string { return headHunterHeaders(public) }

func (o RemainingHTTPDetailOptions) ResourceMatches(source string) bool {
	return source == o.Endpoint || o.Provider == "headhunter" && source == o.SourceURL
}
