package apisniffer

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"html"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type SuccessFactorsLegacyOptions struct{ Host, Company string }

var legacySFHost = regexp.MustCompile(`(?i)^(?:career[0-9]{0,3}|performancemanager[0-9]{1,3})\.(?:successfactors\.(?:com|eu)|sapsf\.(?:com|eu|cn))$`)
var legacySFCompany = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var legacySFLocale = regexp.MustCompile(`^[a-z]{2}(?:_[A-Z]{2})?$`)
var legacySFAjax = regexp.MustCompile(`\bvar\s+ajaxSecKey="([A-Za-z0-9%+/_=-]{8,512})";`)
var legacySFEvent = regexp.MustCompile(`^[A-Za-z0-9._:-]{8,256}$`)
var legacySFGroupedCount = regexp.MustCompile(`^[1-9][0-9]{0,2}(?:,[0-9]{3})+$`)
var legacySFLocation = regexp.MustCompile(`(?i)(?:location|duty\s+station|work\s*(?:place|site)|city|country|region|standort|arbeitsort|lieu|emplacement|ubicaci[oó]n|localit[aà]|sede|地点|工作地|勤務地|근무지)`)

func legacySFURL(raw string, detail bool) (*url.URL, map[string]string, error) {
	u, e := url.Parse(html.UnescapeString(raw))
	if e != nil || len(raw) > 4096 || !strings.EqualFold(u.Scheme, "https") || u.User != nil || u.Opaque != "" || !legacySFHost.MatchString(strings.TrimSuffix(strings.ToLower(u.Hostname()), ".")) || u.Port() != "" && u.Port() != "443" || u.Fragment != "" || !strings.EqualFold(strings.TrimRight(u.Path, "/"), "/career") {
		return nil, nil, ErrOptions
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q) > 12 {
		return nil, nil, ErrOptions
	}
	out := map[string]string{}
	for k, v := range q {
		if len(v) != 1 {
			return nil, nil, ErrOptions
		}
		switch k {
		case "career_ns", "company", "lang", "navBarLevel", "rcm_site_locale", "site":
		case "career_job_req_id":
			if !detail {
				return nil, nil, ErrOptions
			}
		default:
			return nil, nil, ErrOptions
		}
		out[k] = v[0]
	}
	return u, out, nil
}
func legacySFBoard(raw string) (SuccessFactorsLegacyOptions, error) {
	u, q, e := legacySFURL(raw, false)
	if e != nil {
		return SuccessFactorsLegacyOptions{}, e
	}
	company := strings.TrimSpace(q["company"])
	if !legacySFCompany.MatchString(company) || q["career_ns"] != "" && q["career_ns"] != "job_listing_summary" || q["navBarLevel"] != "" && q["navBarLevel"] != "JOB_SEARCH" {
		return SuccessFactorsLegacyOptions{}, ErrOptions
	}
	return SuccessFactorsLegacyOptions{strings.TrimSuffix(strings.ToLower(u.Hostname()), "."), company}, nil
}
func SuccessFactorsLegacyOptionsFromMetadata(board, raw string) (SuccessFactorsLegacyOptions, error) {
	direct, e := legacySFBoard(board)
	if e != nil {
		return direct, e
	}
	md, e := DecodeInlineMetadata(raw)
	if e != nil {
		return direct, e
	}
	if md["preset"] != "successfactors" || md["variant"] != "legacy" {
		return direct, ErrOptions
	}
	for k := range md {
		switch k {
		case "preset", "variant", "host", "company", "listing_url", "jobs", "scraper_type", "scraper_config", "delist_threshold", "drop_threshold", "blast_radius_floor", "suspect_streak", "recent_discovered_counts", "_monitor_config_fingerprint", "_confirmed_drop_candidate":
		default:
			return direct, ErrOptions
		}
	}
	_, hasHost := md["host"]
	_, hasCompany := md["company"]
	if hasHost || hasCompany {
		h, hOK := md["host"].(string)
		c, cOK := md["company"].(string)
		if !hOK || !cOK || strings.TrimSuffix(strings.ToLower(strings.TrimSpace(h)), ".") != direct.Host || strings.TrimSpace(c) != direct.Company {
			return direct, ErrOptions
		}
	}
	if value, present := md["listing_url"]; present && value != nil {
		s, ok := value.(string)
		listed, e := legacySFBoard(s)
		if !ok || e != nil || listed != direct {
			return direct, ErrOptions
		}
	}
	return direct, nil
}
func (o SuccessFactorsLegacyOptions) ListingURL() string {
	return "https://" + o.Host + "/career?company=" + url.QueryEscape(o.Company) + "&career_ns=job_listing_summary&navBarLevel=JOB_SEARCH"
}
func (o SuccessFactorsLegacyOptions) DWRURL(method string) string {
	return "https://" + o.Host + "/xi/ajax/remoting/call/plaincall/careerJobSearchControllerProxy." + method + ".dwr"
}
func (o SuccessFactorsLegacyOptions) ResourceMatches(source string) bool {
	return source == o.ListingURL() || source == o.DWRURL("getInitialJobSearchData") || source == o.DWRURL("search")
}

// Interpret only DWR's data assignment grammar. No JavaScript is executed.
type legacyDWRNode struct {
	object  map[string]any
	array   []any
	isArray bool
}

var legacyDWRDecl = regexp.MustCompile(`^var\s+(s[0-9]+)=(\{\}|\[\]);`)
var legacyDWRAssign = regexp.MustCompile(`^(s[0-9]+)(?:\.([A-Za-z_$][A-Za-z0-9_$]*)|\[([0-9]{1,6})\]|\['((?:\\.|[^'\\]){1,256})'\])=(s[0-9]+|"(?:\\.|[^"\\])*"|null|true|false|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?);`)
var legacyDWRCallback = regexp.MustCompile(`^dwr\.engine\._remoteHandleCallback\('([0-9]+)',\s*'0',\s*(\{(?:payload:s[0-9]+|filters:s[0-9]+,results:s[0-9]+)\})\s*\);`)
var legacyDWRInitialRoot = regexp.MustCompile(`^\{payload:(s[0-9]+)\}$`)
var legacyDWRSearchRoot = regexp.MustCompile(`^\{filters:(s[0-9]+),results:(s[0-9]+)\}$`)

func ParseSuccessFactorsDWR(body string, batch int, initial bool) (map[string]any, error) {
	if len(body) > 5000000 || batch < 0 || batch > 500 {
		return nil, ErrInventory
	}
	body = strings.TrimSpace(body)
	if strings.HasPrefix(body, "throw 'allowScriptTagRemoting is false.';") {
		body = strings.TrimSpace(strings.TrimPrefix(body, "throw 'allowScriptTagRemoting is false.';"))
		if !strings.HasPrefix(body, "//#DWR-INSERT") {
			return nil, ErrInventory
		}
		body = strings.TrimSpace(strings.TrimPrefix(body, "//#DWR-INSERT"))
	}
	if !strings.HasPrefix(body, "//#DWR-REPLY") {
		return nil, ErrInventory
	}
	body = strings.TrimSpace(strings.TrimPrefix(body, "//#DWR-REPLY"))
	objects := map[string]*legacyDWRNode{}
	root := ""
	for statements := 0; body != ""; statements++ {
		if statements > 1000000 {
			return nil, ErrInventory
		}
		if m := legacyDWRDecl.FindStringSubmatch(body); m != nil {
			if objects[m[1]] != nil {
				return nil, ErrInventory
			}
			objects[m[1]] = &legacyDWRNode{object: map[string]any{}, isArray: m[2] == "[]"}
			body = strings.TrimSpace(body[len(m[0]):])
			continue
		}
		if m := legacyDWRAssign.FindStringSubmatch(body); m != nil {
			owner := objects[m[1]]
			if owner == nil {
				return nil, ErrInventory
			}
			var value any
			literal := m[5]
			if strings.HasPrefix(literal, "s") {
				node := objects[literal]
				if node == nil {
					return nil, ErrInventory
				}
				value = node
			} else {
				decoder := json.NewDecoder(strings.NewReader(strings.ReplaceAll(literal, `\'`, "'")))
				decoder.UseNumber()
				if decoder.Decode(&value) != nil {
					return nil, ErrInventory
				}
			}
			if m[2] != "" || m[4] != "" {
				key := m[2]
				if key == "" {
					key = m[4]
				}
				if owner.isArray {
					return nil, ErrInventory
				}
				if _, exists := owner.object[key]; exists {
					return nil, ErrInventory
				}
				owner.object[key] = value
			} else {
				if !owner.isArray {
					return nil, ErrInventory
				}
				index, e := strconv.Atoi(m[3])
				if e != nil || index > 200000 {
					return nil, ErrInventory
				}
				if index < len(owner.array) && owner.array[index] != nil {
					return nil, ErrInventory
				}
				for len(owner.array) <= index {
					owner.array = append(owner.array, nil)
				}
				owner.array[index] = value
			}
			body = strings.TrimSpace(body[len(m[0]):])
			continue
		}
		if m := legacyDWRCallback.FindStringSubmatch(body); m != nil && m[1] == strconv.Itoa(batch) {
			root = m[2]
			if strings.TrimSpace(body[len(m[0]):]) != "" {
				return nil, ErrInventory
			}
			body = ""
			break
		}
		return nil, ErrInventory
	}
	if len(objects) == 0 || root == "" {
		return nil, ErrInventory
	}
	budget := 1000000
	active := map[*legacyDWRNode]bool{}
	var resolve func(any, int) (any, error)
	resolve = func(value any, depth int) (any, error) {
		budget--
		if budget < 0 || depth > 64 {
			return nil, ErrInventory
		}
		node, ok := value.(*legacyDWRNode)
		if !ok {
			return value, nil
		}
		if active[node] {
			return nil, ErrInventory
		}
		active[node] = true
		defer delete(active, node)
		if node.isArray {
			out := []any{}
			for _, value := range node.array {
				v, e := resolve(value, depth+1)
				if e != nil {
					return nil, e
				}
				out = append(out, v)
			}
			return out, nil
		}
		out := map[string]any{}
		for k, value := range node.object {
			v, e := resolve(value, depth+1)
			if e != nil {
				return nil, e
			}
			out[k] = v
		}
		return out, nil
	}
	if initial {
		m := legacyDWRInitialRoot.FindStringSubmatch(root)
		if m == nil || objects[m[1]] == nil {
			return nil, ErrInventory
		}
		v, e := resolve(objects[m[1]], 0)
		out, ok := v.(map[string]any)
		if e != nil || !ok {
			return nil, ErrInventory
		}
		return out, nil
	}
	m := legacyDWRSearchRoot.FindStringSubmatch(root)
	if m == nil {
		return nil, ErrInventory
	}
	out := map[string]any{}
	for i, key := range []string{"filters", "results"} {
		node := objects[m[i+1]]
		if node == nil {
			return nil, ErrInventory
		}
		v, e := resolve(node, 0)
		if _, ok := v.(map[string]any); e != nil || !ok {
			return nil, ErrInventory
		}
		out[key] = v
	}
	return out, nil
}
func legacySFCount(v any) (int, error) {
	if s, ok := v.(string); ok {
		if legacySFGroupedCount.MatchString(s) {
			s = strings.ReplaceAll(s, ",", "")
		}
		if !jobStreetID.MatchString(s) {
			return 0, ErrInventory
		}
		v = json.Number(s)
	}
	i, e := jobStreetCount(v)
	if e != nil || i > 50000 {
		return 0, ErrInventory
	}
	return i, nil
}
func legacySFTotal(root map[string]any) (int, string, error) {
	filters, results := jobStreetMap(root["filters"]), jobStreetMap(root["results"])
	n, e1 := legacySFCount(results["postingCount"])
	f, e2 := legacySFCount(filters["postingCount"])
	p, e3 := legacySFCount(jobStreetMap(jobStreetMap(results["options"])["pagination"])["totalCount"])
	prefix, ok := results["detailURLPrefix"].(string)
	if e1 != nil || e2 != nil || e3 != nil || f != n || p != n || !ok || len(prefix) < 1 || len(prefix) > 2048 {
		return 0, "", ErrInventory
	}
	return n, prefix, nil
}
func legacySFDetailURL(o SuccessFactorsLegacyOptions, prefix, id string) (string, string, error) {
	base, _ := url.Parse("https://" + o.Host)
	ref, e := url.Parse(prefix + id)
	if e != nil {
		return "", "", ErrInventory
	}
	u, q, e := legacySFURL(base.ResolveReference(ref).String(), true)
	if e != nil || strings.TrimSuffix(strings.ToLower(u.Hostname()), ".") != o.Host || q["company"] != o.Company || q["career_ns"] != "job_listing" || q["navBarLevel"] != "JOB_SEARCH" || q["career_job_req_id"] != id {
		return "", "", ErrInventory
	}
	result := "https://" + o.Host + "/career?career_ns=job_listing&company=" + url.QueryEscape(o.Company) + "&navBarLevel=JOB_SEARCH"
	locale := q["rcm_site_locale"]
	if locale != "" {
		result += "&rcm_site_locale=" + url.QueryEscape(locale)
	}
	for _, k := range []string{"site", "lang"} {
		if q[k] != "" {
			result += "&" + k + "=" + url.QueryEscape(q[k])
		}
	}
	return result + "&career_job_req_id=" + id, locale, nil
}
func legacySFLabels(filters map[string]any) map[string]string {
	out := map[string]string{}
	rows, _ := jobStreetMap(filters["configs"])["filters"].([]any)
	for _, v := range rows {
		m := jobStreetMap(v)
		name, nOK := m["fieldName"].(string)
		label, lOK := m["label"].(string)
		name = strings.TrimPrefix(name, "customFilter_")
		label = strings.TrimSpace(label)
		if nOK && lOK && name != "" && len([]rune(label)) >= 1 && len([]rune(label)) <= 200 {
			out[name] = label
		}
	}
	return out
}
func legacySFDate(raw, locale string) string {
	parts := strings.Split(strings.TrimSpace(raw), "/")
	if len(parts) != 3 || len(parts[0]) < 1 || len(parts[0]) > 2 || len(parts[1]) < 1 || len(parts[1]) > 2 || len(parts[2]) != 4 {
		return ""
	}
	a, e1 := strconv.Atoi(parts[0])
	b, e2 := strconv.Atoi(parts[1])
	y, e3 := strconv.Atoi(parts[2])
	if e1 != nil || e2 != nil || e3 != nil || y < 1 || y > 9999 {
		return ""
	}
	month, day := b, a
	if locale == "en_US" {
		month, day = a, b
	}
	date := time.Date(y, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if date.Year() != y || int(date.Month()) != month || date.Day() != day {
		return ""
	}
	return date.Format("2006-01-02")
}
func ParseSuccessFactorsLegacyJobs(postings any, o SuccessFactorsLegacyOptions, prefix string, labels map[string]string) ([]map[string]any, error) {
	rows, ok := postings.([]any)
	if !ok {
		return nil, ErrInventory
	}
	out := []map[string]any{}
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		id := jobStreetScalar(row["id"])
		n, e := strconv.ParseInt(id, 10, 64)
		if e != nil || n < 1 || n > 999999999999999999 {
			return nil, ErrInventory
		}
		id = strconv.FormatInt(n, 10)
		title := jobStreetClean(row["title"])
		if len([]rune(title)) < 1 || len([]rune(title)) > 500 {
			return nil, ErrInventory
		}
		source, prefixLocale, e := legacySFDetailURL(o, prefix, id)
		if e != nil {
			return nil, e
		}
		locale, _ := row["defaultLocale"].(string)
		if !legacySFLocale.MatchString(locale) {
			locale = prefixLocale
		}
		md := map[string]any{"id": id}
		if locale != "" {
			md["default_locale"] = locale
		}
		dateRaw, sOK := row["postingDate"].(string)
		if sOK && strings.TrimSpace(dateRaw) != "" {
			md["posting_date_raw"] = strings.TrimSpace(dateRaw)
		}
		values := map[string][]string{}
		order := []string{}
		var walk func(any, int)
		walk = func(v any, depth int) {
			if depth > 5 {
				return
			}
			if rows, ok := v.([]any); ok {
				for _, row := range rows {
					walk(row, depth+1)
				}
				return
			}
			m := jobStreetMap(v)
			field, ok := m["fieldId"].(string)
			text, valid := m["longVal"].(string)
			if !valid || strings.TrimSpace(text) == "" {
				text, valid = m["shortVal"].(string)
			}
			if !ok || !valid || strings.TrimSpace(text) == "" {
				return
			}
			text = jobStreetClean(text)
			if _, exists := values[field]; !exists {
				order = append(order, field)
			}
			for _, old := range values[field] {
				if old == text {
					return
				}
			}
			values[field] = append(values[field], text)
		}
		walk(row["otherValues"], 0)
		locations := []string{}
		seen := map[string]bool{}
		fields := map[string]any{}
		for _, field := range order {
			label := labels[field]
			if legacySFLocation.MatchString(label) {
				for _, value := range values[field] {
					if !seen[value] {
						locations = append(locations, value)
						seen[value] = true
					}
				}
			}
			if label == "" {
				label = field
			}
			if len(values[field]) == 1 {
				fields[label] = values[field][0]
			} else {
				fields[label] = values[field]
			}
		}
		if len(fields) > 0 {
			md["fields"] = fields
		}
		job := map[string]any{"url": source, "title": title, "metadata": md}
		if len(locations) > 0 {
			job["locations"] = locations
		}
		if locale != "" {
			language := locale
			if len(language) > 2 {
				language = language[:2]
			}
			job["language"] = strings.ToLower(language)
		}
		if date := legacySFDate(dateRaw, locale); date != "" {
			job["date_posted"] = date
		}
		out = append(out, job)
	}
	return out, nil
}

type SuccessFactorsLegacyFetch func(context.Context, Request) ([]byte, http.Header, error)
type legacySFSession struct {
	options              SuccessFactorsLegacyOptions
	token, event, script string
}

func (s legacySFSession) request(page int) Request {
	method := "search"
	lines := []string{fmt.Sprintf("c0-e1=number:%d", page), "c0-e2=number:100", "c0-e3=Object_Object:{currentPage:reference:c0-e1,pageSize:reference:c0-e2}", "c0-e4=string:JOB_POSTING_DATE", "c0-e5=string:DESC", "c0-param0=Object_Object:{pagination:reference:c0-e3,sortByColumn:reference:c0-e4,sortOrder:reference:c0-e5}"}
	if page == 0 {
		method = "getInitialJobSearchData"
		lines = []string{"c0-e1=string:", "c0-e2=string:", "c0-e3=boolean:false", "c0-e4=string:Etc%2FUTC", "c0-param0=Object_Object:{filterOnly:reference:c0-e1,jobAlertId:reference:c0-e2,returnToList:reference:c0-e3,browserTimeZone:reference:c0-e4}"}
	}
	u, _ := url.Parse(s.options.ListingURL())
	envelope := []string{"callCount=1", "page=" + u.RequestURI(), "httpSessionId=", "scriptSessionId=" + s.script, "c0-scriptName=careerJobSearchControllerProxy", "c0-methodName=" + method, "c0-id=0"}
	envelope = append(envelope, lines...)
	envelope = append(envelope, fmt.Sprintf("batchId=%d", page), "")
	headers := http.Header{"Content-Type": {"text/plain"}, "Origin": {"https://" + s.options.Host}, "Referer": {s.options.ListingURL()}, "Viewid": {"/ui/rcmcareer/pages/careersite/career.jsp.xhtml"}, "X-Ajax-Token": {s.token}, "X-Csrf-Token": {s.token}, "X-Event-Id": {s.event}, "X-Sap-Page-Info": {"companyId=" + s.options.Company}, "X-Subaction": {"0"}}
	return Request{Method: "POST", URL: s.options.DWRURL(method), Body: strings.Join(envelope, "\n"), Headers: headers}
}

type SuccessFactorsLegacyInventory struct {
	Jobs      []map[string]any
	Truncated bool
}

func DiscoverSuccessFactorsLegacy(ctx context.Context, o SuccessFactorsLegacyOptions, fetch SuccessFactorsLegacyFetch, emit ...func([]map[string]any) error) (SuccessFactorsLegacyInventory, error) {
	fail := func(e error) (SuccessFactorsLegacyInventory, error) { return SuccessFactorsLegacyInventory{}, e }
	if ctx == nil || fetch == nil {
		return fail(ErrOptions)
	}
	if ctx.Err() != nil {
		return fail(ctx.Err())
	}
	body, headers, e := fetch(ctx, Request{Method: "GET", URL: o.ListingURL()})
	if e != nil {
		return fail(e)
	}
	document := string(body)
	token := legacySFAjax.FindStringSubmatch(document)
	event := headers.Get("X-Event-Id")
	if len(body) > 5000000 || !strings.Contains(document, "careerJobSearchController") || !strings.Contains(document, "getInitialJobSearchData") || !strings.Contains(document, `companyId: "`+o.Company+`"`) && !strings.Contains(document, "companyId="+o.Company) || token == nil || !legacySFEvent.MatchString(event) {
		return fail(ErrInventory)
	}
	random := make([]byte, 12)
	if _, e = rand.Read(random); e != nil {
		return fail(e)
	}
	session := legacySFSession{o, token[1], event, hex.EncodeToString(random)}
	body, _, e = fetch(ctx, session.request(0))
	if e != nil {
		return fail(e)
	}
	root, e := ParseSuccessFactorsDWR(string(body), 0, true)
	if e != nil {
		return fail(e)
	}
	total, prefix, e := legacySFTotal(root)
	if e != nil {
		return fail(e)
	}
	initial, ok := jobStreetMap(root["results"])["postings"].([]any)
	if !ok || len(initial) != min(total, 10) {
		return fail(ErrInventory)
	}
	labels := legacySFLabels(jobStreetMap(root["filters"]))
	out := SuccessFactorsLegacyInventory{Jobs: []map[string]any{}}
	seen := map[string]bool{}
	seenURLs := map[string]bool{}
	for page := 1; page <= (total+99)/100; page++ {
		if ctx.Err() != nil {
			return fail(ctx.Err())
		}
		body, _, e = fetch(ctx, session.request(page))
		if e != nil {
			return fail(e)
		}
		root, e = ParseSuccessFactorsDWR(string(body), page, false)
		if e != nil {
			return fail(e)
		}
		count, current, e := legacySFTotal(root)
		if e != nil || count != total || current != prefix {
			return fail(ErrInventory)
		}
		results := jobStreetMap(root["results"])
		pagination := jobStreetMap(jobStreetMap(results["options"])["pagination"])
		for k, expected := range map[string]int{"currentPage": page, "pageSize": 100, "startRow": (page-1)*100 + 1, "endRow": page * 100} {
			value, e := legacySFCount(pagination[k])
			if e != nil || value != expected {
				return fail(ErrInventory)
			}
		}
		jobs, e := ParseSuccessFactorsLegacyJobs(results["postings"], o, prefix, labels)
		if e != nil {
			return fail(e)
		}
		expected := min(100, total-(page-1)*100)
		if len(jobs) != expected {
			if len(jobs) >= expected || total <= 1024 || len(seen)+len(jobs) != 1024 {
				return fail(ErrInventory)
			}
			out.Truncated = true
		}
		for _, job := range jobs {
			id := job["metadata"].(map[string]any)["id"].(string)
			source := job["url"].(string)
			if seen[id] || seenURLs[source] {
				return fail(ErrInventory)
			}
			seen[id] = true
			seenURLs[source] = true
			out.Jobs = append(out.Jobs, job)
		}
		if len(emit) > 0 && emit[0] != nil {
			if e := emit[0](jobs); e != nil {
				return fail(e)
			}
		}
		if out.Truncated {
			break
		}
	}
	if len(seen) != total && !out.Truncated {
		return fail(ErrInventory)
	}
	return out, ctx.Err()
}
