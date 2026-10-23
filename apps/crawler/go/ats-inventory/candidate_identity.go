package main

import (
	"encoding/base32"
	"encoding/hex"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	"golang.org/x/net/idna"
	"golang.org/x/text/unicode/norm"
)

type inventoryCandidate struct {
	Family         string   `json:"family"`
	NativeATS      string   `json:"native_ats"`
	Tenant         string   `json:"tenant"`
	SourceKey      string   `json:"source_key"`
	Name           string   `json:"name"`
	Slug           string   `json:"slug"`
	BoardURL       string   `json:"board_url"`
	ImpactUnknown  bool     `json:"impact_unknown"`
	ActiveJobs     int      `json:"active_jobs"`
	RemoteJobs     int      `json:"remote_jobs"`
	LocationCount  int      `json:"location_count"`
	CountryCodes   []string `json:"country_codes"`
	LatestPostedAt *string  `json:"latest_posted_at"`
}

func (c inventoryCandidate) sourceHash() string   { return sha256Bytes([]byte(c.SourceKey)) }
func (c inventoryCandidate) boardURLHash() string { return sha256Bytes([]byte(c.BoardURL)) }
func nativeATSIdentity(family string, c compatibility) string {
	if c.Kind == "native" && c.MonitorType != nil {
		return "native:" + *c.MonitorType
	}
	return "family:" + family
}
func candidateFromImpact(company companyImpact, registry map[string]compatibility) (inventoryCandidate, error) {
	var empty inventoryCandidate
	c, ok := registry[company.ATS]
	if !inventoryFamilyPattern.MatchString(company.ATS) || !ok || !c.candidateEligible() {
		return empty, fmt.Errorf("ATS family is not candidate eligible")
	}
	board, e := normalizeBoardURL(company.URL)
	if e != nil {
		return empty, e
	}
	tenant := candidateTenantKey(company.ATS, board, nil)
	if tenant == "" || utf8.RuneCountInString(tenant) > 240 {
		tenant = "url-sha256:" + sha256Bytes([]byte(board))
	}
	component := strings.ReplaceAll(url.QueryEscape(tenant), "+", "%20")
	if len(component) > 400 {
		component = "tenant-sha256-" + sha256Bytes([]byte(tenant))
	}
	return inventoryCandidate{Family: company.ATS, NativeATS: nativeATSIdentity(company.ATS, c), Tenant: tenant, SourceKey: "ats-scrapers:" + company.ATS + ":" + component, Name: pythonInventoryStrip(company.Name), Slug: pythonInventoryStrip(company.Slug), BoardURL: board, ImpactUnknown: company.ImpactUnknown, ActiveJobs: company.ActiveJobs, RemoteJobs: company.RemoteJobs, LocationCount: company.LocationCount, CountryCodes: nonnilStrings(company.CountryCodes), LatestPostedAt: company.LatestPostedAt}, nil
}
func pythonInventoryStrip(s string) string {
	return strings.TrimFunc(s, func(r rune) bool {
		return strings.ContainsRune("\t\n\v\f\r\x1c\x1d\x1e\x1f", r) || r == ' ' || r == 0x85 || r == 0xa0 || r == 0x1680 || r >= 0x2000 && r <= 0x200a || r == 0x2028 || r == 0x2029 || r == 0x202f || r == 0x205f || r == 0x3000
	})
}

type inventoryQueryPair struct{ key, value string }

func inventoryUnquote(value string) string {
	value = strings.ReplaceAll(value, "+", " ")
	var out strings.Builder
	for i := 0; i < len(value); i++ {
		if value[i] == '%' && i+2 < len(value) {
			if b, e := hex.DecodeString(value[i+1 : i+3]); e == nil {
				out.WriteByte(b[0])
				i += 2
				continue
			}
		}
		out.WriteByte(value[i])
	}
	return strings.ToValidUTF8(out.String(), "\ufffd")
}
func inventoryQueryPairs(query string, keepBlank bool) []inventoryQueryPair {
	out := []inventoryQueryPair{}
	for _, pair := range strings.Split(query, "&") {
		if pair == "" {
			continue
		}
		k, v, has := strings.Cut(pair, "=")
		if !has {
			v = ""
		}
		k, v = inventoryUnquote(k), inventoryUnquote(v)
		if keepBlank || v != "" {
			out = append(out, inventoryQueryPair{k, v})
		}
	}
	return out
}
func inventoryIDNAHost(host string) (string, error) {
	labels := strings.Split(host, ".")
	for i, label := range labels {
		ascii := true
		for _, r := range label {
			if r >= 128 {
				ascii = false
				break
			}
		}
		if !ascii {
			var e error
			label, e = idna.New(idna.MapForLookup(), idna.Transitional(true), idna.VerifyDNSLength(false)).ToASCII(label)
			if e != nil {
				return "", e
			}
		}
		if len(label) == 0 || len(label) > 63 {
			return "", fmt.Errorf("invalid IDNA label")
		}
		labels[i] = label
	}
	return strings.Join(labels, "."), nil
}

func normalizeBoardURL(value string) (string, error) {
	if pythonInventoryStrip(value) == "" || utf8.RuneCountInString(value) > 4096 {
		return "", fmt.Errorf("board URL is empty or too long")
	}
	text := pythonInventoryStrip(value)
	u, e := checkedURL(text)
	if e != nil || u.Hostname() == "" || u.Scheme != "http" && u.Scheme != "https" {
		return "", fmt.Errorf("board URL must be HTTP(S) with a hostname")
	}
	if u.User != nil {
		password, _ := u.User.Password()
		if u.User.Username() != "" || password != "" {
			return "", fmt.Errorf("board URL contains credentials")
		}
	}
	host := strings.TrimRight(u.Hostname(), ".")
	if !strings.Contains(host, ":") {
		host, e = inventoryIDNAHost(host)
		if e != nil {
			return "", fmt.Errorf("board URL hostname is invalid")
		}
	}
	host = unicodeFold(host)
	if strings.Contains(host, ":") && !strings.HasPrefix(host, "[") {
		host = "[" + host + "]"
	}
	if port := u.Port(); port != "" {
		n, _ := strconvInventoryPort(port)
		if n != 80 && n != 443 {
			host += ":" + fmt.Sprint(n)
		}
	}
	path := rawURLPath(text, u)
	if path == "" {
		path = "/"
	}
	if path != "/" {
		path = strings.TrimRight(path, "/")
		if path == "" {
			path = "/"
		}
	}
	pairs := []inventoryQueryPair{}
	for _, pair := range inventoryQueryPairs(u.RawQuery, true) {
		lower := unicodeFold(pair.key)
		if strings.HasPrefix(lower, "utm_") || lower == "fbclid" || lower == "gclid" || lower == "mc_cid" || lower == "mc_eid" {
			continue
		}
		pairs = append(pairs, pair)
	}
	sort.SliceStable(pairs, func(i, j int) bool {
		if pairs[i].key != pairs[j].key {
			return pairs[i].key < pairs[j].key
		}
		return pairs[i].value < pairs[j].value
	})
	query := make([]string, 0, len(pairs))
	for _, pair := range pairs {
		query = append(query, url.QueryEscape(pair.key)+"="+url.QueryEscape(pair.value))
	}
	result := "https://" + host + path
	if len(query) > 0 {
		result += "?" + strings.Join(query, "&")
	}
	return result, nil
}
func strconvInventoryPort(port string) (int, error) {
	n := 0
	for _, v := range port {
		if v < '0' || v > '9' {
			return 0, fmt.Errorf("invalid port")
		}
		n = n*10 + int(v-'0')
	}
	return n, nil
}

var workdayStandardHost = regexp.MustCompile(`^([a-z0-9_-]+)\.(wd[0-9]+)\.myworkdayjobs\.com$`)
var workdayCustomHost = regexp.MustCompile(`^(wd[0-9]+)\.myworkdaysite\.com$`)
var inventoryLocaleSegment = regexp.MustCompile(`^[a-z]{2}-[a-z]{2}$`)

func candidateTenantKey(family, board string, config map[string]any) string {
	u, e := checkedURL(board)
	if e != nil {
		return ""
	}
	segments := []string{}
	for _, s := range strings.Split(rawURLPath(board, u), "/") {
		if s != "" {
			segments = append(segments, unicodeFold(s))
		}
	}
	after := func(label string) string {
		for i, v := range segments {
			if v == label && i+1 < len(segments) {
				return segments[i+1]
			}
		}
		return ""
	}
	query := map[string]string{}
	for _, p := range inventoryQueryPairs(u.RawQuery, false) {
		query[unicodeFold(p.key)] = p.value
	}
	token := ""
	keys := map[string][]string{"ashby": {"token"}, "gem": {"token", "slug"}, "greenhouse": {"token", "board_token"}, "lever": {"token"}, "rippling": {"slug"}, "smartrecruiters": {"token"}, "workable": {"token"}}[family]
	for _, key := range keys {
		if s, ok := config[key].(string); ok {
			s = pythonInventoryStrip(s)
			if s != "" && utf8.RuneCountInString(s) <= 240 {
				token = unicodeFold(s)
				break
			}
		}
	}
	if token == "" {
		label := map[string]string{"greenhouse": "boards", "ashby": "job-board", "lever": "postings", "smartrecruiters": "companies", "gem": "v0", "rippling": "board", "workable": "accounts"}[family]
		if label != "" {
			if family == "greenhouse" {
				v := query["for"]
				if v == "" {
					v = query["url_token"]
				}
				if v != "" {
					token = unicodeFold(pythonInventoryStrip(v))
				}
			}
			if token == "" {
				token = after(label)
			}
			if token == "" && len(segments) > 0 {
				if family != "greenhouse" && family != "ashby" || family == "greenhouse" && segments[0] != "embed" && segments[0] != "v1" || family == "ashby" && segments[0] != "posting-api" {
					token = segments[0]
				}
			}
		}
	}
	if token != "" {
		return family + ":" + token
	}
	host := unicodeFold(u.Hostname())
	if family == "workday" {
		configured := func(key string) string {
			v := config[key]
			if v == nil || v == false || v == "" {
				return ""
			}
			return unicodeFold(pythonInventoryStrip(fmt.Sprint(v)))
		}
		company, instance, site := configured("company"), configured("wd_instance"), configured("site")
		if company != "" && instance != "" && site != "" {
			return "workday:" + company + ":" + instance + ":" + site
		}
		if m := workdayStandardHost.FindStringSubmatch(host); m != nil {
			scoped := segments
			if len(scoped) > 0 && inventoryLocaleSegment.MatchString(scoped[0]) {
				scoped = scoped[1:]
			}
			if len(scoped) > 0 {
				return "workday:" + m[1] + ":" + m[2] + ":" + scoped[0]
			}
		}
		if m := workdayCustomHost.FindStringSubmatch(host); m != nil && len(segments) >= 3 && segments[0] == "recruiting" {
			return "workday:" + segments[1] + ":" + m[1] + ":" + segments[2]
		}
	}
	if family == "jobvite" && len(segments) > 0 {
		v := segments[0]
		if v == "careers" && len(segments) > 1 {
			v = segments[1]
		}
		return "jobvite:" + v
	}
	if family == "moka" {
		for _, label := range []string{"social-recruitment", "campus-recruitment"} {
			for i, v := range segments {
				if v == label && i+2 < len(segments) {
					return "moka:" + label + ":" + segments[i+1] + ":" + segments[i+2]
				}
			}
		}
	}
	if family == "successfactors" {
		if company := unicodeFold(pythonInventoryStrip(query["company"])); company != "" && host != "" {
			return "successfactors:" + host + ":" + company
		}
	}
	if family == "taleo" {
		base := inventoryTenantKey(family, board, nil)
		if cws := unicodeFold(pythonInventoryStrip(query["cws"])); base != "" && cws != "" {
			return base + ":cws:" + cws
		}
	}
	if family == "keka" && host != "" {
		scope := ""
		if len(segments) > 1 && segments[0] == "careers" {
			scope = ":" + segments[1]
		}
		return "keka:" + host + scope
	}
	return inventoryTenantKey(family, board, nil)
}
func workdayTenantWildcard(tenant string) string {
	if strings.HasPrefix(tenant, "workday:") && strings.Count(tenant, ":") == 3 {
		return tenant[:strings.LastIndex(tenant, ":")] + ":*"
	}
	return ""
}

var inventoryCandidateMarkerPattern = regexp.MustCompile(`<!-- ats-inventory-candidate:v1 source=([a-z2-7]+) board=([0-9a-f]{64}) -->`)

func candidateMarker(source, board string) (string, error) {
	normalized, e := normalizeBoardURL(board)
	if e != nil {
		return "", e
	}
	encoded := strings.ToLower(base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString([]byte(source)))
	return "<!-- ats-inventory-candidate:v1 source=" + encoded + " board=" + sha256Bytes([]byte(normalized)) + " -->", nil
}

type candidateMarkerPair struct{ SourceKey, BoardHash string }

func parseCandidateMarkers(body string) []candidateMarkerPair {
	out := []candidateMarkerPair{}
	for _, m := range inventoryCandidateMarkerPattern.FindAllStringSubmatch(body, -1) {
		raw, e := base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(strings.ToUpper(m[1]))
		if e != nil || !utf8.Valid(raw) {
			continue
		}
		s := string(raw)
		if strings.HasPrefix(s, "ats-scrapers:") && utf8.RuneCountInString(s) <= 600 {
			out = append(out, candidateMarkerPair{s, m[2]})
		}
	}
	return out
}

var inventoryLabelPattern = regexp.MustCompile(`[^a-z0-9]+`)
var inventoryIssuePrefixPattern = regexp.MustCompile(`(?i)^(?:\[ats inventory\]\s*)?(?:add|import|configure|monitor)\s+(?:company\s*:\s*)?`)

func normalizeInventoryLabel(value string) string {
	var ascii strings.Builder
	for _, r := range norm.NFKD.String(value) {
		if r < 128 {
			ascii.WriteRune(r)
		}
	}
	return strings.TrimSpace(inventoryLabelPattern.ReplaceAllString(unicodeFold(ascii.String()), " "))
}
func normalizeInventoryIssueTitle(value string) string {
	return normalizeInventoryLabel(inventoryIssuePrefixPattern.ReplaceAllString(pythonInventoryStrip(value), ""))
}
func inventoryWebsiteDomain(value string) string {
	u, e := checkedURL(value)
	if e != nil || u.Hostname() == "" || u.Scheme != "http" && u.Scheme != "https" {
		return ""
	}
	host := strings.TrimPrefix(strings.TrimRight(unicodeFold(u.Hostname()), "."), "www.")
	parts := strings.Split(host, ".")
	if len(parts) <= 2 {
		return host
	}
	suffix := strings.Join(parts[len(parts)-2:], ".")
	switch suffix {
	case "ac.uk", "co.jp", "co.nz", "co.uk", "com.au", "com.br", "com.mx", "com.sg", "org.uk":
		return strings.Join(parts[len(parts)-3:], ".")
	}
	return suffix
}
