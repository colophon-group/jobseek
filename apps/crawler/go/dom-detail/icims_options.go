package dom

// iCIMS inventory uses the existing verified worker transport and URL writer.
// These options and parsers are pure; they cannot fetch or grant queue ownership.
import (
	"errors"
	"golang.org/x/text/cases"
	"net/url"
	"regexp"
	"sort"
	"strings"
)

var ErrICIMS = errors.New("invalid iCIMS inventory")
var icimsHostPattern = regexp.MustCompile(`(?i)^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.icims\.com$`)
var icimsPathPattern = regexp.MustCompile(`(?i)^(?:/jobs(?:/search|/[\p{Nd}]+(?:/[^/?#]+)?/job)?)?/?$`)
var icimsJobPath = regexp.MustCompile(`(?i)^/jobs/([\p{Nd}]+)(?:/[^/?#]+)?/job/?$`)

type ICIMSOptions struct {
	Host, JibeURL, PeerHost            string
	JobHosts, JibeHosts, IDDedupeHosts []string
	TitleAliases                       map[string]string
}

func icimsHost(v any) string {
	s, ok := v.(string)
	if !ok {
		return ""
	}
	s = strings.ToLower(strings.TrimRight(strings.TrimSpace(s), "."))
	if !icimsHostPattern.MatchString(s) {
		return ""
	}
	switch s {
	case "api.icims.com", "app.icims.com", "help.icims.com", "support.icims.com", "www.icims.com":
		return ""
	}
	return s
}
func icimsNormalize(s string) string {
	return cases.Fold().String(strings.Join(strings.Fields(s), " "))
}
func icimsURLHost(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || (u.Port() != "" && u.Port() != "443") || !icimsPathPattern.MatchString(u.Path) {
		return ""
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return ""
	}
	for k, v := range q {
		if len(v) != 1 {
			return ""
		}
		switch k {
		case "ss", "in_iframe":
			if v[0] != "1" {
				return ""
			}
		case "o", "schemaId":
			if v[0] != "" {
				return ""
			}
		case "searchRelation":
			if v[0] != "keyword_all" {
				return ""
			}
		case "pr":
			if !regexp.MustCompile(`^[\p{Nd}]+$`).MatchString(v[0]) {
				return ""
			}
		default:
			return ""
		}
	}
	return icimsHost(u.Hostname())
}
func icimsHosts(v any) ([]string, error) {
	values, ok := v.([]any)
	if !ok || len(values) == 0 || len(values) > 20 {
		return nil, ErrICIMS
	}
	out := []string{}
	seen := map[string]bool{}
	for _, v := range values {
		h := icimsHost(v)
		if h == "" || seen[h] {
			return nil, ErrICIMS
		}
		seen[h] = true
		out = append(out, h)
	}
	sort.Strings(out)
	return out, nil
}
func hasICIMSHost(hosts []string, host string) bool {
	for _, h := range hosts {
		if h == host {
			return true
		}
	}
	return false
}
func ICIMSOptionsFromMetadata(boardURL string, metadata Object) (ICIMSOptions, error) {
	o := ICIMSOptions{TitleAliases: map[string]string{}}
	o.Host = icimsHost(metadata["host"])
	if o.Host == "" {
		o.Host = icimsURLHost(boardURL)
	}
	if o.Host == "" {
		return o, ErrICIMS
	}
	o.JobHosts = []string{o.Host}
	if v := metadata["job_hosts"]; v != nil {
		hosts, err := icimsHosts(v)
		if err != nil {
			return o, err
		}
		o.JobHosts = hosts
		if !hasICIMSHost(hosts, o.Host) {
			o.JobHosts = append(o.JobHosts, o.Host)
			sort.Strings(o.JobHosts)
		}
	}
	if v := metadata["dedupe_job_ids_from_hosts"]; v != nil {
		hosts, err := icimsHosts(v)
		if err != nil || hasICIMSHost(hosts, o.Host) {
			return o, ErrICIMS
		}
		o.IDDedupeHosts = hosts
	}
	if metadata["jibe_url"] != nil || metadata["jibe_job_hosts"] != nil {
		s, ok := metadata["jibe_url"].(string)
		if !ok {
			return o, ErrICIMS
		}
		u, err := url.Parse(s)
		if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || (u.Port() != "" && u.Port() != "443") || strings.TrimRight(u.Path, "/") != "/jobs" || u.RawQuery != "" || u.Fragment != "" || u.Opaque != "" {
			return o, ErrICIMS
		}
		hosts, err := icimsHosts(metadata["jibe_job_hosts"])
		if err != nil || !hasICIMSHost(hosts, o.Host) {
			return o, ErrICIMS
		}
		o.JibeURL = strings.TrimRight(s, "/")
		o.JibeHosts = hosts
	}
	if v := metadata["cross_locale_dedupe"]; v != nil {
		m, ok := v.(map[string]any)
		if !ok {
			return o, ErrICIMS
		}
		for k := range m {
			if k != "peer_host" && k != "title_aliases" {
				return o, ErrICIMS
			}
		}
		o.PeerHost = icimsHost(m["peer_host"])
		if o.PeerHost == "" || o.PeerHost == o.Host {
			return o, ErrICIMS
		}
		if v, exists := m["title_aliases"]; exists {
			aliases, ok := v.(map[string]any)
			if !ok || len(aliases) > 100 {
				return o, ErrICIMS
			}
			for k, v := range aliases {
				target, ok := v.(string)
				if !ok || strings.TrimSpace(k) == "" || strings.TrimSpace(target) == "" || len([]rune(k)) > 200 || len([]rune(target)) > 200 {
					return o, ErrICIMS
				}
				a, b := icimsNormalize(k), icimsNormalize(target)
				if old, ok := o.TitleAliases[a]; ok && old != b {
					return o, ErrICIMS
				}
				o.TitleAliases[a] = b
			}
		}
	}
	return o, nil
}
func ICIMSListingURL(host string, page int) string {
	if page == 0 {
		return "https://" + host + "/jobs/search?ss=1&in_iframe=1"
	}
	return "https://" + host + "/jobs/search?pr=" + strconvICIMS(page) + "&in_iframe=1&searchRelation=keyword_all&schemaId=&o="
}
func ICIMSCanonicalJobURL(raw string, hosts []string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || (u.Port() != "" && u.Port() != "443") {
		return ""
	}
	host := strings.ToLower(u.Hostname())
	if !hasICIMSHost(hosts, host) {
		return ""
	}
	path := u.Path
	if u.RawPath != "" {
		path = u.RawPath
	}
	m := icimsJobPath.FindStringSubmatch(path)
	if m == nil {
		return ""
	}
	return "https://" + host + "/jobs/" + m[1] + "/job?in_iframe=1"
}
