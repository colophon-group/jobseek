package apisniffer

import (
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type UmantisOptions struct {
	BoardURL, Origin, Listing, Employer, EmployerField, EmptyText string
	Strict                                                        bool
}

var umantisHost = regexp.MustCompile(`^recruitingapp-([0-9]+)(?:\.([a-z0-9-]+))?\.umantis\.com$`)
var umantisCustomer = regexp.MustCompile(`^[0-9]{1,12}$`)
var umantisField = regexp.MustCompile(`^column_value_[1-9][0-9]{0,11}$`)

func UmantisOptionsFromMetadata(board, raw string) (UmantisOptions, error) {
	o := UmantisOptions{BoardURL: board}
	if strings.TrimSpace(raw) == "" {
		raw = "{}"
	}
	m, err := DecodeInlineMetadata(raw)
	u, parseErr := url.Parse(board)
	if err != nil || parseErr != nil || !validURL(board) || u.Scheme != "https" || u.User != nil || u.Port() != "" && u.Port() != "443" {
		return o, ErrOptions
	}
	customer, region, cname := "", "", ""
	for key, target := range map[string]*string{"customer_id": &customer, "region": &region, "cname": &cname} {
		if value, exists := m[key]; exists {
			text, ok := value.(string)
			if !ok {
				return o, ErrOptions
			}
			*target = text
		}
	}
	if customer == "" {
		if matched := umantisHost.FindStringSubmatch(strings.ToLower(u.Hostname())); matched != nil {
			customer, region = matched[1], matched[2]
		} else if strings.HasSuffix(strings.ToLower(u.Hostname()), ".umantis.com") {
			cname = strings.ToLower(u.Hostname())
		} else {
			return o, ErrOptions
		}
	}
	if cname != "" {
		if !regexp.MustCompile(`^[a-z0-9][a-z0-9.-]{0,190}\.umantis\.com$`).MatchString(cname) {
			return o, ErrOptions
		}
		o.Origin = "https://" + cname
	} else {
		if !umantisCustomer.MatchString(customer) || region != "" && !regexp.MustCompile(`^[a-z0-9-]{1,32}$`).MatchString(region) {
			return o, ErrOptions
		}
		o.Origin = "https://recruitingapp-" + customer + "."
		if region != "" {
			o.Origin += region + "."
		}
		o.Origin += "umantis.com"
	}
	path := "/Jobs/All"
	if v, exists := m["listing_path"]; exists {
		var ok bool
		path, ok = v.(string)
		if !ok {
			return o, ErrOptions
		}
	}
	v, err := url.Parse(path)
	if err != nil || v.IsAbs() || v.Host != "" || v.User != nil || !strings.HasPrefix(v.Path, "/Jobs/") || v.Fragment != "" || len(path) > 8192 || strings.ContainsAny(path, "\x00\r\n") {
		return o, ErrOptions
	}
	o.Listing = o.Origin + path
	if strict, exists := m["strict_listing_contract"]; exists {
		var ok bool
		o.Strict, ok = strict.(bool)
		if !ok {
			return o, ErrOptions
		}
	}
	if o.Strict {
		for key, target := range map[string]*string{"expected_employer": &o.Employer, "employer_field_id": &o.EmployerField, "empty_state_text": &o.EmptyText} {
			text, ok := m[key].(string)
			if !ok || strings.TrimSpace(text) == "" || len([]rune(text)) > 256 || strings.ContainsRune(text, '\x00') {
				return o, ErrOptions
			}
			*target = strings.TrimSpace(text)
		}
		if !umantisField.MatchString(o.EmployerField) {
			return o, ErrOptions
		}
	}
	for key := range m {
		switch key {
		case "customer_id", "region", "cname", "listing_path", "strict_listing_contract", "expected_employer", "employer_field_id", "empty_state_text", "proxy", "scraper_type", "scraper_config", "suspect_streak", "recent_discovered_counts", "_confirmed_drop_candidate", "_monitor_config_fingerprint", "delist_threshold", "drop_threshold", "blast_radius_floor":
		default:
			return o, ErrOptions
		}
	}
	return o, nil
}

func (o UmantisOptions) ResourceMatches(raw string) bool {
	u, err := url.Parse(raw)
	root, rootErr := url.Parse(o.Origin)
	return err == nil && rootErr == nil && u.Scheme == "https" && u.User == nil && strings.EqualFold(u.Host, root.Host) && u.Fragment == "" && len(raw) <= 8192 && (strings.HasPrefix(u.Path, "/Jobs/") || strings.HasPrefix(u.Path, "/Vacancies/"))
}

func (o UmantisOptions) PaginationURL(table string, page int) (string, error) {
	if !regexp.MustCompile(`^[0-9]{1,12}$`).MatchString(table) || page < 2 || page > 100 {
		return "", ErrOptions
	}
	u, err := url.Parse(o.Listing)
	if err != nil {
		return "", ErrOptions
	}
	pairs := []string{}
	for _, pair := range strings.Split(u.RawQuery, "&") {
		if pair == "" {
			continue
		}
		key, value, _ := strings.Cut(pair, "=")
		key, err = url.QueryUnescape(key)
		if err != nil {
			return "", ErrOptions
		}
		value, err = url.QueryUnescape(value)
		if err != nil {
			return "", ErrOptions
		}
		if key != "tc"+table && !strings.EqualFold(key, "reset") {
			pairs = append(pairs, url.QueryEscape(key)+"="+url.QueryEscape(value))
		}
	}
	pairs = append(pairs, "tc"+table+"=p"+strconv.Itoa(page))
	u.RawQuery = strings.Join(pairs, "&")
	u.Fragment = ""
	return u.String(), nil
}
