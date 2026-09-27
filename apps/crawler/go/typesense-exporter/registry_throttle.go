package main

import (
	"encoding/json"
	"html"
	"net"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

func registryString(v any) string { s, _ := v.(string); return s }

func registryURL(raw string) *url.URL {
	// urllib removes embedded tab/newline characters and leading C0/space.
	raw = strings.TrimLeftFunc(raw, func(r rune) bool { return r <= 32 })
	raw = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(raw)
	u, err := url.Parse(raw)
	if err != nil {
		return nil
	}
	return u
}

func registrySafeHTTPS(raw string) *url.URL {
	u := registryURL(raw)
	if u == nil || strings.ToLower(u.Scheme) != "https" || u.User != nil || u.Hostname() == "" || (u.Port() != "" && u.Port() != "443") {
		return nil
	}
	return u
}

func registryURLPath(u *url.URL) string {
	path := u.EscapedPath()
	// urlparse separates the final segment's parameters from .path.
	last := strings.LastIndex(path, "/")
	if i := strings.Index(path[last+1:], ";"); i >= 0 {
		path = path[:last+1+i]
	}
	return path
}

var registryTenant = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var registryDarwinCompany = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$`)
var registryDarwinJob = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$`)

func registryDarwinHost(raw string) string {
	host := strings.TrimRight(strings.ToLower(registryTrim(raw)), ".")
	for _, suffix := range []string{".darwinbox.in", ".darwinbox.com"} {
		if !strings.HasSuffix(host, suffix) {
			continue
		}
		tenant := strings.TrimSuffix(host, suffix)
		switch tenant {
		case "api", "app", "help", "static", "support", "www":
			return ""
		}
		if registryTenant.MatchString(tenant) {
			return host
		}
	}
	return ""
}

func registryDarwinRequestHost(raw string, metadata map[string]any) string {
	company, exists := metadata["company_id"]
	if !exists {
		company = "main"
	}
	if host := registryDarwinHost(registryString(metadata["host"])); host != "" && registryDarwinCompany.MatchString(registryTrim(registryString(company))) {
		return host
	}
	if len(raw) > 4096 {
		return ""
	}
	u := registrySafeHTTPS(raw)
	if u == nil || u.RawQuery != "" || u.Fragment != "" {
		return ""
	}
	host := registryDarwinHost(u.Hostname())
	if host == "" {
		return ""
	}
	parts := strings.FieldsFunc(registryURLPath(u), func(r rune) bool { return r == '/' })
	if len(parts) < 3 || strings.ToLower(parts[0]) != "ms" {
		return ""
	}
	version, tail := strings.ToLower(parts[1]), parts[2:]
	if version == "candidate" && strings.ToLower(tail[0]) == "careers" {
		tail = tail[1:]
	} else {
		if (version != "candidate" && version != "candidatev2") || len(tail) < 2 || strings.ToLower(tail[1]) != "careers" || !registryDarwinCompany.MatchString(registryTrim(tail[0])) {
			return ""
		}
		tail = tail[2:]
	}
	if len(tail) > 0 && (len(tail) != 2 || strings.ToLower(tail[0]) != "jobdetails" || !registryDarwinJob.MatchString(registryTrim(tail[1]))) {
		return ""
	}
	return host
}

var registryAvatureID = regexp.MustCompile(`^[1-9][\p{Nd}]{0,19}$`)
var registryAvatureSegment = regexp.MustCompile(`^[^/?#\x00-\x20]{1,160}$`)

func registryAvatureHost(raw string) string {
	u := registrySafeHTTPS(raw)
	if u == nil {
		return ""
	}
	host := strings.TrimRight(cases.Fold().String(u.Hostname()), ".")
	if !strings.Contains(host, ".") || net.ParseIP(host) != nil {
		return ""
	}
	switch host {
	case "avature.net", "localhost", "localhost.localdomain", "www.avature.net":
		return ""
	}
	parts := strings.FieldsFunc(registryURLPath(u), func(r rune) bool { return r == '/' })
	for _, part := range parts {
		if !registryAvatureSegment.MatchString(part) {
			return ""
		}
	}
	for i, part := range parts {
		switch strings.ToLower(part) {
		case "searchjobs", "searchjobsmaps":
			if i == len(parts)-1 && u.RawQuery == "" {
				return host
			}
			return ""
		}
	}
	for i, part := range parts {
		key := map[string]string{"jobdetail": "jobId", "folderdetail": "folderId", "pipelinedetail": "pipelineId"}[strings.ToLower(part)]
		if key == "" {
			continue
		}
		if i == 0 {
			return ""
		}
		tail := parts[i+1:]
		if len(tail) > 0 {
			if len(tail) >= 2 && registryAvatureID.MatchString(tail[len(tail)-1]) && u.RawQuery == "" {
				return host
			}
			return ""
		}
		params, err := url.ParseQuery(u.RawQuery)
		if err == nil && len(params) == 1 && len(params[key]) == 1 && registryAvatureID.MatchString(params.Get(key)) {
			return host
		}
		return ""
	}
	return ""
}

var registryTaleoHost = regexp.MustCompile(`^[a-z]{3}\.tbe\.taleo\.net$`)
var registryTaleoPartition = regexp.MustCompile(`^[a-z]{3}[0-9]{2}$`)
var registryTaleoOrg = regexp.MustCompile(`^[A-Z0-9][A-Z0-9_-]{0,63}$`)
var registryTaleoPositive = regexp.MustCompile(`^[1-9][0-9]{0,9}$`)
var registryTaleoPath = regexp.MustCompile(`(?i)^/([a-z]{3}[0-9]{2})/ats/careers/v2/(searchResults|viewRequisition)/?$`)

func registryTaleoPositiveID(v any) bool {
	switch value := v.(type) {
	case string:
		return registryTaleoPositive.MatchString(registryTrim(value))
	case json.Number:
		return registryTaleoPositive.MatchString(string(value))
	default:
		return false
	}
}
func registryTaleoMetadataHost(metadata map[string]any) string {
	host := strings.ToLower(registryTrim(registryString(metadata["host"])))
	partition := strings.ToLower(registryTrim(registryString(metadata["partition"])))
	org := cases.Upper(language.Und).String(registryTrim(registryString(metadata["org"])))
	if !registryTaleoHost.MatchString(host) || !registryTaleoPartition.MatchString(partition) || !strings.HasPrefix(partition, host[:3]) || !registryTaleoOrg.MatchString(org) || !registryTaleoPositiveID(metadata["cws"]) {
		return ""
	}
	return host
}
func registryTaleoRequestHost(raw string, metadata map[string]any) string {
	if host := registryTaleoMetadataHost(metadata); host != "" {
		return host
	}
	if len(raw) > 4096 {
		return ""
	}
	u := registrySafeHTTPS(html.UnescapeString(raw))
	if u == nil || u.Fragment != "" || strings.Count(u.RawQuery, "&") >= 8 {
		return ""
	}
	match := registryTaleoPath.FindStringSubmatch(registryURLPath(u))
	if match == nil {
		return ""
	}
	params, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return ""
	}
	for _, values := range params {
		if len(values) != 1 {
			return ""
		}
	}
	host := registryTaleoMetadataHost(map[string]any{"host": u.Hostname(), "partition": match[1], "org": params.Get("org"), "cws": params.Get("cws")})
	if host == "" {
		return ""
	}
	if strings.ToLower(match[2]) == "searchresults" {
		for key := range params {
			if key != "org" && key != "cws" && key != "rowFrom" {
				return ""
			}
		}
		if value, exists := params["rowFrom"]; exists {
			if value[0] == "" || strings.IndexFunc(value[0], func(r rune) bool { return r < '0' || r > '9' }) >= 0 {
				return ""
			}
			n, err := strconv.Atoi(value[0])
			if err != nil || n > 49990 || n%10 != 0 {
				return ""
			}
		}
	} else if len(params) != 3 || !registryTaleoPositiveID(params.Get("rid")) {
		return ""
	}
	return host
}

func registryThrottleKey(name, raw string, metadata map[string]any, routes registryRoutes) string {
	if name == "darwinbox" {
		if host := registryDarwinRequestHost(raw, metadata); host != "" {
			return host
		}
	}
	if name == "avature" {
		if host := registryAvatureHost(registryString(metadata["listing_url"])); host != "" {
			return host
		}
		if host := registryAvatureHost(raw); host != "" {
			return host
		}
	}
	if name == "pageup" {
		return "careers.pageuppeople.com"
	}
	for _, api := range routes.APIMonitors {
		if api == name {
			return name
		}
	}
	if name == "taleo" {
		if host := registryTaleoRequestHost(raw, metadata); host != "" {
			return host
		}
	}
	if u := registryURL(raw); u != nil && u.Hostname() != "" {
		return strings.ToLower(u.Hostname())
	}
	return raw
}
