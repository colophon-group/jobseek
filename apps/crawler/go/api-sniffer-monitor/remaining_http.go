package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strconv"
	"strings"

	xhtml "golang.org/x/net/html"
)

func remainingID(value any) string {
	switch v := value.(type) {
	case string:
		if smallUnsigned.MatchString(v) {
			return v
		}
	case json.Number:
		if smallUnsigned.MatchString(string(v)) {
			return string(v)
		}
	}
	return ""
}
func remainingPositiveID(value any) string {
	id := remainingID(value)
	if !johdiOfferID.MatchString(id) {
		return ""
	}
	return id
}
func remainingObject(raw []byte) (map[string]any, error) {
	d, e := Decode(raw)
	if e != nil {
		return nil, ErrInventory
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	return m, nil
}
func remainingText(value any) string {
	s, ok := value.(string)
	if !ok {
		return ""
	}
	return strings.Join(strings.Fields(s), " ")
}
func remainingNodeText(node *xhtml.Node) string {
	var b strings.Builder
	var visit func(*xhtml.Node)
	visit = func(n *xhtml.Node) {
		if n.Type == xhtml.TextNode {
			b.WriteString(n.Data)
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			visit(c)
		}
	}
	visit(node)
	return b.String()
}

func DiscoverJohdi(ctx context.Context, o RemainingHTTPOptions, fetch SmallProviderFetch) ([]string, bool, error) {
	page, e := fetch(ctx, Request{Method: "GET", URL: o.BoardURL})
	if e != nil {
		return nil, false, e
	}
	doc, e := xhtml.Parse(strings.NewReader(string(page)))
	if e != nil {
		return nil, false, ErrInventory
	}
	matches := 0
	valid := false
	var visit func(*xhtml.Node)
	visit = func(n *xhtml.Node) {
		if n.Type == xhtml.ElementNode {
			a := map[string]string{}
			for _, attr := range n.Attr {
				a[strings.ToLower(attr.Key)] = attr.Val
			}
			if a["id"] == "ats-offers" {
				matches++
				valid = strings.TrimSpace(a["data-company-hash-key"]) == o.CompanyKey && strings.TrimSpace(a["data-flow"]) == o.Flow && strings.TrimSpace(a["data-locale"]) == o.Locale
			}
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			visit(c)
		}
	}
	visit(doc)
	if matches != 1 || !valid {
		return nil, false, ErrInventory
	}
	raw, e := fetch(ctx, Request{Method: "GET", URL: o.JohdiListURL(), Headers: http.Header{"Accept": {"application/json"}}})
	if e != nil {
		return nil, false, e
	}
	d, e := Decode(raw)
	if e != nil {
		return nil, false, ErrInventory
	}
	rows, ok := d.Value.([]any)
	if !ok {
		return nil, false, ErrInventory
	}
	urls := []string{}
	seen := map[string]bool{}
	for _, value := range rows {
		row, ok := value.(map[string]any)
		if !ok {
			return nil, false, ErrInventory
		}
		id := remainingPositiveID(row["id"])
		if id == "" || seen[id] {
			return nil, false, ErrInventory
		}
		seen[id] = true
		urls = append(urls, strings.TrimRight(o.BoardURL, "/")+"#/offer/"+id+"/job")
	}
	sort.Strings(urls)
	return urls, len(urls) > 50000, nil
}

func jobDivaSnapshot(ctx context.Context, o RemainingHTTPOptions, fetch SmallProviderFetch) ([]string, bool, error) {
	bootstrap := http.Header{"Authorization": {"Basic YXhlbG9uOmF4ZWxvbg=="}, "Portalid": {"1"}, "A": {o.Tenant}, "Compid": {"-1"}}
	raw, e := fetch(ctx, Request{Method: "GET", URL: o.ListingURL(), Headers: bootstrap})
	if e != nil {
		return nil, false, e
	}
	auth, e := remainingObject(raw)
	if e != nil {
		return nil, false, e
	}
	scalar := func(value any) (string, bool) {
		switch v := value.(type) {
		case string:
			return v, true
		case json.Number:
			return string(v), true
		case bool:
			if v {
				return "True", true
			}
			return "False", true
		}
		return "", false
	}
	token, tokenOK := scalar(auth["token"])
	portal, portalOK := scalar(auth["portalID"])
	if !tokenOK || !portalOK {
		return nil, false, ErrInventory
	}
	tenant := o.Tenant
	if detailTruthy(auth["a"]) {
		var ok bool
		tenant, ok = scalar(auth["a"])
		if !ok || tenant != o.Tenant {
			return nil, false, ErrInventory
		}
	}
	comp := "-1"
	if v, present := auth["compid"]; present {
		var ok bool
		comp, ok = scalar(v)
		if !ok {
			return nil, false, ErrInventory
		}
	}
	headers := http.Header{"Token": {token}, "Portalid": {portal}, "A": {tenant}, "Compid": {comp}, "Content-Type": {"application/x-www-form-urlencoded"}, "Referer": {"https://www2.jobdiva.com/"}}
	form := url.Values{"city": {""}, "country": {""}, "from": {"1"}, "jobCategories": {""}, "jobDivisions": {""}, "jobTypes": {""}, "keywords": {""}, "miles": {""}, "onsiteFlex": {""}, "portalID": {"1"}, "qualifications": {""}, "states": {""}, "to": {"200"}, "unit": {"mi"}, "zipcode": {""}}
	raw, e = fetch(ctx, Request{Method: "POST", URL: JobDivaAPI + "/job/searchjobsportal", Body: form.Encode(), Headers: headers})
	if e != nil {
		return nil, false, e
	}
	first, e := remainingObject(raw)
	if e != nil {
		return nil, false, e
	}
	total, e := smallInt(first["total"], false)
	rows, ok := first["data"].([]any)
	if e != nil || total < 0 || total > 50000 || !ok || len(rows) > 200 || (total == 0) != (len(rows) == 0) {
		return nil, false, ErrInventory
	}
	ids := []string{}
	seen := map[string]bool{}
	appendRows := func(rows []any) bool {
		for _, value := range rows {
			row, ok := value.(map[string]any)
			if !ok {
				return false
			}
			n, ok := row["id"].(json.Number)
			if !ok {
				return false
			}
			id := remainingPositiveID(n)
			if id == "" || seen[id] {
				return false
			}
			seen[id] = true
			ids = append(ids, id)
		}
		return true
	}
	expected := min(total, 200)
	if len(rows) != expected {
		return ids, false, nil
	}
	if !appendRows(rows) {
		return ids, false, nil
	}
	pages := max(1, (total+199)/200)
	for page := 2; page <= pages; page++ {
		count := 200
		if page == pages {
			count = 201
		}
		q := url.Values{"from": {strconv.Itoa((page-1)*200 + 1)}, "to": {"0"}, "count": {strconv.Itoa(count)}, "portaltype": {"1"}}
		raw, e = fetch(ctx, Request{Method: "GET", URL: JobDivaAPI + "/job/getmore?" + q.Encode(), Headers: headers})
		if e != nil {
			return nil, false, e
		}
		payload, e := remainingObject(raw)
		if e != nil {
			return nil, false, e
		}
		rows, ok = payload["data"].([]any)
		if !ok || len(rows) > count {
			return nil, false, ErrInventory
		}
		for _, v := range rows {
			if _, ok := v.(map[string]any); !ok {
				return nil, false, ErrInventory
			}
		}
		visible := rows[:min(len(rows), 200)]
		expected = min(200, total-len(ids))
		if !appendRows(visible[:min(len(visible), expected)]) || len(visible) != expected {
			return ids, false, nil
		}
	}
	return ids, true, nil
}

func DiscoverJobDiva(ctx context.Context, o RemainingHTTPOptions, fetch SmallProviderFetch) ([]string, bool, error) {
	var previous []string
	previousValid := false
	accumulated := map[string]bool{}
	urls := func(ids []string) []string {
		result := []string{}
		for _, id := range ids {
			result = append(result, "https://www2.jobdiva.com/portal/?a="+url.QueryEscape(o.Tenant)+"&compid=0#/jobs/"+id)
		}
		sort.Strings(result)
		return result
	}
	for attempt := 0; attempt < 4; attempt++ {
		ids, consistent, e := jobDivaSnapshot(ctx, o, fetch)
		if e != nil {
			return nil, false, e
		}
		for _, id := range ids {
			accumulated[id] = true
		}
		if !consistent {
			continue
		}
		if previousValid && reflect.DeepEqual(previous, ids) {
			return urls(ids), false, nil
		}
		previous, previousValid = ids, true
	}
	ids := []string{}
	for id := range accumulated {
		ids = append(ids, id)
	}
	return urls(ids), true, nil
}

type HeadHunterHTTPStatus interface{ HTTPStatusCode() int }

func headHunterHeaders(public bool) http.Header {
	accept := "application/json"
	if public {
		accept = "text/html,application/xhtml+xml"
	}
	return http.Header{"Accept": {accept}, "User-Agent": {"Jobseek/1.0 (+https://jobseek.ch; contact@jobseek.ch)"}}
}

func HeadHunterFields(row map[string]any, employer, host string) (map[string]any, error) {
	id := remainingID(row["id"])
	emp, _ := row["employer"].(map[string]any)
	if id == "" || remainingID(emp["id"]) != employer || !headHunterHosts[host] {
		return nil, ErrInventory
	}
	fields := map[string]any{"url": "https://" + host + "/vacancy/" + id}
	for _, pair := range [][2]string{{"name", "title"}, {"published_at", "date_posted"}} {
		if s := remainingText(row[pair[0]]); s != "" {
			fields[pair[1]] = s
		}
	}
	if s, ok := row["description"].(string); ok {
		fields["description"] = s
	}
	name := func(v any) string { m, _ := v.(map[string]any); return remainingText(m["name"]) }
	address, _ := row["address"].(map[string]any)
	location := remainingText(address["raw"])
	if location == "" {
		parts := []string{}
		seen := map[string]bool{}
		for _, k := range []string{"city", "street", "building"} {
			s := remainingText(address[k])
			if s != "" && !seen[s] {
				parts = append(parts, s)
				seen[s] = true
			}
		}
		location = strings.Join(parts, ", ")
	}
	if location == "" {
		location = name(row["area"])
	}
	if location != "" {
		fields["locations"] = []string{location}
	}
	employment, ok := row["employment_form"].(map[string]any)
	if !ok {
		employment, _ = row["employment"].(map[string]any)
	}
	kind := remainingText(employment["id"])
	if kind == "" {
		kind = remainingText(employment["name"])
	}
	if kind != "" {
		fields["employment_type"] = strings.ToLower(kind)
	}
	formats, _ := row["work_format"].([]any)
	formatIDs := map[string]bool{}
	for _, v := range formats {
		m, _ := v.(map[string]any)
		formatIDs[strings.ToUpper(remainingText(m["id"]))] = true
	}
	for _, pair := range [][2]string{{"HYBRID", "hybrid"}, {"REMOTE", "remote"}, {"ON_SITE", "onsite"}} {
		if formatIDs[pair[0]] {
			fields["job_location_type"] = pair[1]
			break
		}
	}
	if fields["job_location_type"] == nil {
		schedule, _ := row["schedule"].(map[string]any)
		if strings.ToLower(remainingText(schedule["id"])) == "remote" {
			fields["job_location_type"] = "remote"
		}
	}
	salary, modern := row["salary_range"].(map[string]any)
	unit := "month"
	if modern {
		mode, _ := salary["mode"].(map[string]any)
		unit = map[string]string{"MONTH": "month", "HOUR": "hour"}[strings.ToUpper(remainingText(mode["id"]))]
	} else {
		salary, _ = row["salary"].(map[string]any)
	}
	currency := remainingText(salary["currency"])
	low, high := providerNumber(salary["from"], false), providerNumber(salary["to"], false)
	if unit != "" && currency != "" && (low != nil || high != nil) {
		fields["base_salary"] = map[string]any{"currency": currency, "min": low, "max": high, "unit": unit}
	}
	extra := map[string]any{}
	for _, key := range []string{"key_skills", "professional_roles", "languages"} {
		values, _ := row[key].([]any)
		names := []string{}
		for _, v := range values {
			if s := name(v); s != "" {
				names = append(names, s)
			}
		}
		if len(names) > 0 {
			target := key
			if key == "key_skills" {
				target = "skills"
			}
			extra[target] = names
		}
	}
	if len(extra) > 0 {
		fields["extras"] = extra
	}
	metadata := map[string]any{"vacancy_id": id, "headhunter_employer_id": employer}
	if s := name(emp); s != "" {
		metadata["employer"] = s
	}
	for _, k := range []string{"department", "experience", "schedule"} {
		if s := name(row[k]); s != "" {
			metadata[k] = s
		}
	}
	fields["metadata"] = metadata
	return fields, nil
}

func HeadHunterPublicFields(raw []byte, o RemainingHTTPOptions) ([]map[string]any, error) {
	doc, e := xhtml.Parse(strings.NewReader(string(raw)))
	if e != nil {
		return nil, ErrInventory
	}
	var state []byte
	matches := 0
	var visit func(*xhtml.Node)
	visit = func(n *xhtml.Node) {
		if n.Type == xhtml.ElementNode && n.Data == "template" {
			for _, a := range n.Attr {
				if a.Key == "id" && a.Val == "HH-Lux-InitialState" {
					matches++
					state = []byte(remainingNodeText(n))
				}
			}
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			visit(c)
		}
	}
	visit(doc)
	if matches != 1 {
		return nil, ErrInventory
	}
	payload, e := remainingObject(state)
	if e != nil {
		return nil, e
	}
	result, _ := payload["vacancySearchResult"].(map[string]any)
	rows, ok := result["vacancies"].([]any)
	total, e := smallInt(result["totalResults"], false)
	if !ok || e != nil || total < 0 || total != len(rows) {
		return nil, ErrInventory
	}
	jobs := []map[string]any{}
	seen := map[string]bool{}
	for _, v := range rows {
		row, ok := v.(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		id := remainingID(row["vacancyId"])
		company, _ := row["company"].(map[string]any)
		if id == "" || remainingID(company["id"]) != o.Tenant || seen[id] {
			return nil, ErrInventory
		}
		seen[id] = true
		fields := map[string]any{"url": "https://" + o.Host + "/vacancy/" + id, "metadata": map[string]any{"vacancy_id": id, "headhunter_employer_id": o.Tenant}}
		if s := remainingText(row["name"]); s != "" {
			fields["title"] = s
		}
		address, _ := row["address"].(map[string]any)
		location := remainingText(address["displayName"])
		if location == "" {
			area, _ := row["area"].(map[string]any)
			location = remainingText(area["name"])
		}
		if location != "" {
			fields["locations"] = []string{location}
		}
		publication, _ := row["publicationTime"].(map[string]any)
		if s := remainingText(publication["$"]); s != "" {
			fields["date_posted"] = s
		}
		jobs = append(jobs, fields)
	}
	return jobs, nil
}

func DiscoverHeadHunter(ctx context.Context, o RemainingHTTPOptions, fetch SmallProviderFetch) ([]map[string]any, bool, error) {
	jobs := []map[string]any{}
	seen := map[string]bool{}
	expectedFound, expectedPages := -1, -1
	for page := 0; expectedPages < 0 || page < max(1, expectedPages); page++ {
		raw, e := fetch(ctx, o.HeadHunterRequest(page))
		if e != nil {
			var status HeadHunterHTTPStatus
			if errors.As(e, &status) && status.HTTPStatusCode() == 403 {
				raw, e = fetch(ctx, Request{Method: "GET", URL: o.HeadHunterPublicURL(), Headers: headHunterHeaders(true)})
				if e != nil {
					return nil, false, e
				}
				jobs, e = HeadHunterPublicFields(raw, o)
				return jobs, false, e
			}
			return nil, false, e
		}
		payload, e := remainingObject(raw)
		if e != nil {
			return nil, false, e
		}
		rows, ok := payload["items"].([]any)
		if !ok {
			return nil, false, ErrInventory
		}
		numbers := map[string]int{}
		for _, key := range []string{"found", "page", "pages", "per_page"} {
			n, e := smallInt(payload[key], false)
			if e != nil || n < 0 {
				return nil, false, ErrInventory
			}
			numbers[key] = n
		}
		found, pages := numbers["found"], numbers["pages"]
		if numbers["page"] != page || numbers["per_page"] != 100 || pages != min((found+99)/100, 20) {
			return nil, false, ErrInventory
		}
		if expectedFound < 0 {
			expectedFound, expectedPages = found, pages
		} else if expectedFound != found || expectedPages != pages {
			return nil, false, ErrInventory
		}
		for _, v := range rows {
			row, ok := v.(map[string]any)
			if !ok {
				return nil, false, ErrInventory
			}
			fields, e := HeadHunterFields(row, o.Tenant, o.Host)
			if e != nil {
				return nil, false, e
			}
			source := fields["url"].(string)
			if seen[source] {
				return nil, false, ErrInventory
			}
			seen[source] = true
			jobs = append(jobs, fields)
		}
		if len(jobs) > 2000 {
			return nil, false, ErrInventory
		}
	}
	if len(jobs) != min(expectedFound, 2000) {
		return nil, false, ErrInventory
	}
	return jobs, expectedFound > 2000, nil
}
