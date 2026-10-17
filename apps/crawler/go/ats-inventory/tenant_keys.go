package main

import (
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

var tenantUUIDPattern = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

func normalizeInventoryIdentity(value any, limit int) string {
	if value == nil {
		return ""
	}
	var text string
	switch v := value.(type) {
	case string:
		text = v
	case bool:
		if v {
			text = "True"
		} else {
			text = "False"
		}
	default:
		text = fmt.Sprint(v)
	}
	text = unicodeFold(strings.Join(strings.FieldsFunc(text, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }), " "))
	if utf8.RuneCountInString(text) > limit {
		return ""
	}
	return text
}
func inventoryTenantKey(family string, value, atsID any) string {
	fallback := func() string {
		if family != "paylocity" {
			return ""
		}
		v := normalizeInventoryIdentity(atsID, 500)
		id := strings.SplitN(v, ":", 2)[0]
		if tenantUUIDPattern.MatchString(id) {
			return "paylocity:" + id
		}
		return ""
	}
	text, ok := value.(string)
	if !ok || utf8.RuneCountInString(text) > 4096 {
		return fallback()
	}
	text = strings.TrimLeftFunc(text, func(r rune) bool { return r <= 32 })
	text = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(text)
	u, e := url.Parse(text)
	if e != nil || u.Hostname() == "" || u.Scheme != "http" && u.Scheme != "https" {
		return fallback()
	}
	host := strings.TrimRight(unicodeFold(u.Hostname()), ".")
	origin := "host:" + host
	path := rawURLPath(text, u)
	parts := []string{}
	for _, v := range strings.Split(path, "/") {
		if v != "" {
			parts = append(parts, unicodeFold(v))
		}
	}
	first := func() string {
		if len(parts) == 0 {
			return ""
		}
		return parts[0]
	}
	after := func(label string) string {
		for i, v := range parts {
			if v == label {
				if i+1 < len(parts) {
					return parts[i+1]
				}
				return ""
			}
		}
		return ""
	}
	query := map[string][]string{}
	rawQuery, _ := url.ParseQuery(u.RawQuery)
	for k, v := range rawQuery {
		query[unicodeFold(k)] = v
	}
	q := func(k string) string {
		if v := query[k]; len(v) > 0 {
			return normalizeInventoryIdentity(v[0], 500)
		}
		return ""
	}
	switch family {
	case "ashby", "gem", "greenhouse", "jobvite", "lever", "rippling", "smartrecruiters", "workable":
		if first() != "" {
			return "hostpath:" + host + "/" + first()
		}
		return ""
	case "herp", "hrmos", "join_com":
		label := map[string]string{"herp": "v1", "hrmos": "pages", "join_com": "companies"}[family]
		if v := after(label); v != "" {
			return "hostpath:" + host + "/" + label + "/" + v
		}
		return ""
	case "adp":
		if cid := q("cid"); cid != "" {
			return "adp:" + cid + "/" + q("ccid")
		}
		return ""
	case "cornerstone":
		client, site := q("c"), after("careersite")
		if client == "" && site == "" {
			return origin
		}
		return "cornerstone:" + host + "/" + client + "/" + site
	case "dayforce":
		if first() == "" {
			return ""
		}
		portal := ""
		if len(parts) > 1 && parts[1] != "jobs" && parts[1] != "job" {
			portal = parts[1]
		}
		return "dayforce:" + first() + "/" + portal
	case "moka":
		for i, v := range parts {
			if v == "social-recruitment" {
				if i+2 < len(parts) {
					return "moka:" + parts[i+1] + "/" + parts[i+2]
				}
				return origin
			}
		}
		return origin
	case "oracle":
		site := q("site_number")
		if site == "" {
			site = after("sites")
		}
		return "oracle:" + host + "/" + site
	case "pageup":
		if first() != "" {
			return "pageup:" + first()
		}
		return ""
	case "paycom":
		if v := after("portal"); v != "" {
			return "paycom:" + v
		}
		return ""
	case "paylocity":
		if v := fallback(); v != "" {
			return v
		}
		for i := len(parts) - 1; i >= 0; i-- {
			if tenantUUIDPattern.MatchString(parts[i]) {
				return "paylocity:" + parts[i]
			}
		}
		return ""
	case "softgarden":
		for _, suffix := range []string{".career.softgarden.de", ".softgarden.io"} {
			if strings.HasSuffix(host, suffix) {
				tenant := strings.TrimSuffix(host, suffix)
				if tenant != "" && !strings.Contains(tenant, ".") {
					return "softgarden:" + tenant
				}
			}
		}
		return origin
	case "taleo":
		if org := q("org"); org != "" {
			return "taleo:" + host + "/" + first() + "/" + org
		}
		return ""
	case "ukg":
		if board := after("jobboard"); board != "" {
			return "ukg:" + first() + "/" + board
		}
		return ""
	case "workday":
		if first() != "" {
			return "workday:" + host + "/" + first()
		}
		return ""
	default:
		return origin
	}
}
