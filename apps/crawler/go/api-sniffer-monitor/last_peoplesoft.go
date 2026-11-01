package apisniffer

import (
	"context"
	"net/http"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	xhtml "golang.org/x/net/html"
)

var peopleSoftCount = regexp.MustCompile(`(?i)<b>\s*([0-9]{1,6})\s*</b>\s*jobs?\s+found`)
var peopleSoftFacetLabel = regexp.MustCompile(`^Apply (.+) \(([0-9]{1,6})\) filter$`)
var peopleSoftFacetAction = regexp.MustCompile(`^PTS_SELECT\$[0-9]{1,4}$`)

const peopleSoftMore = "HRS_AGNT_RSLT_I$hdown$0"

func lastNodeAttribute(n *xhtml.Node, key string) (string, bool) {
	for _, a := range n.Attr {
		if a.Key == key {
			return a.Val, true
		}
	}
	return "", false
}

func peopleSoftValue(row *xhtml.Node, prefix string) string {
	n := cascadia.Query(row, cascadia.MustCompile(`span[id^='`+prefix+`$']`))
	if n == nil {
		return ""
	}
	// Lexbor text(strip=True) trims each text node before concatenation.
	return strings.Join(strings.FieldsFunc(paylocityText(n, ""), enterpriseSpace), " ")
}

func peopleSoftDate(s string) (any, error) {
	if s == "" {
		return nil, nil
	}
	parts := strings.Split(s, "/")
	if len(parts) != 3 || len(parts[0]) < 1 || len(parts[0]) > 2 || len(parts[1]) < 1 || len(parts[1]) > 2 || len(parts[2]) != 4 {
		return nil, ErrField
	}
	numbers := make([]int, 3)
	for i, p := range parts {
		if !smallUnsigned.MatchString(p) {
			return nil, ErrField
		}
		v, e := strconv.Atoi(p)
		if e != nil {
			return nil, ErrField
		}
		numbers[i] = v
	}
	m, d, y := numbers[0], numbers[1], numbers[2]
	date := time.Date(y, time.Month(m), d, 0, 0, 0, 0, time.UTC)
	if y < 1 || date.Year() != y || int(date.Month()) != m || date.Day() != d {
		return nil, ErrField
	}
	return date.Format("2006-01-02"), nil
}

func parsePeopleSoftListing(source string, o LastHTTPOptions, allowIdenticalDuplicates bool) ([]Job, int, error) {
	if !strings.Contains(source, "HRS_AGNT_RSLT_I") || !strings.Contains(source, "Search Results List") {
		return nil, 0, ErrInventory
	}
	count := peopleSoftCount.FindStringSubmatch(source)
	if len(count) != 2 {
		return nil, 0, ErrInventory
	}
	expected, e := strconv.Atoi(count[1])
	if e != nil {
		return nil, 0, ErrInventory
	}
	doc, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return nil, 0, e
	}
	jobs := []Job{}
	seen := map[string]Job{}
	for _, row := range cascadia.QueryAll(doc, cascadia.MustCompile(`li[id^='HRS_AGNT_RSLT_I$0_row_']`)) {
		id, title, location := peopleSoftValue(row, "HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID"), peopleSoftValue(row, "SCH_JOB_TITLE"), peopleSoftValue(row, "LOCATION")
		if !peopleSoftID.MatchString(id) || title == "" || location == "" {
			return nil, 0, ErrInventory
		}
		date, e := peopleSoftDate(peopleSoftValue(row, "SCH_OPENED"))
		if e != nil {
			return nil, 0, e
		}
		metadata := map[string]any{"job_id": id}
		for source, key := range map[string]string{"HRS_APP_JBSCH_I_HRS_DEPT_DESCR": "department", "JOB_FAMILY_LABEL": "job_family"} {
			if value := peopleSoftValue(row, source); value != "" {
				metadata[key] = value
			}
		}
		if allowIdenticalDuplicates {
			for source, key := range map[string]string{"HRS_BU_DESCR": "business_unit", "HRS_CLS_DT_DESCR": "close_date"} {
				if value := peopleSoftValue(row, source); value != "" {
					metadata[key] = value
				}
			}
		}
		job := Job{URL: o.PeopleSoftJobURL(id), Title: title, Locations: []string{location}, DatePosted: date, Metadata: metadata}
		if previous, exists := seen[id]; exists && (!allowIdenticalDuplicates || !reflect.DeepEqual(previous, job)) {
			return nil, 0, ErrInventory
		}
		seen[id] = job
		jobs = append(jobs, job)
	}
	return jobs, expected, nil
}

func ParsePeopleSoftListing(source string, o LastHTTPOptions) ([]Job, int, error) {
	return parsePeopleSoftListing(source, o, false)
}

func PeopleSoftContinuation(source string) (string, bool, error) {
	doc, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return "", false, e
	}
	more := cascadia.Query(doc, cascadia.MustCompile("div.ps_box-more"))
	if more == nil {
		return "", false, nil
	}
	onclick, _ := lastNodeAttribute(more, "onclick")
	if !strings.Contains(onclick, peopleSoftMore) {
		return "", false, nil
	}
	fields := [][2]string{}
	for _, n := range cascadia.QueryAll(doc, cascadia.MustCompile("form input[name]")) {
		kind, _ := lastNodeAttribute(n, "type")
		if kind == "" {
			kind = "text"
		}
		kind = strings.ToLower(kind)
		_, disabled := lastNodeAttribute(n, "disabled")
		_, checked := lastNodeAttribute(n, "checked")
		if disabled || kind == "button" || kind == "file" || kind == "image" || kind == "reset" || kind == "submit" || (kind == "checkbox" || kind == "radio") && !checked {
			continue
		}
		name, _ := lastNodeAttribute(n, "name")
		if name == "" || name == "ICAction" || name == "ICFocus" || name == "ICXPos" || name == "ICYPos" {
			continue
		}
		value, _ := lastNodeAttribute(n, "value")
		fields = append(fields, [2]string{name, value})
	}
	fields = append(fields, [2]string{"ICAction", peopleSoftMore}, [2]string{"ICFocus", peopleSoftMore}, [2]string{"ICXPos", "0"}, [2]string{"ICYPos", "0"})
	return lastQuery(fields), true, nil
}

type peopleSoftFacet struct {
	Name, Action string
	Count        int
}

func peopleSoftBusinessUnitFacets(source string, expected int) ([]peopleSoftFacet, error) {
	doc, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return nil, e
	}
	groups := []*xhtml.Node{}
	for _, group := range cascadia.QueryAll(doc, cascadia.MustCompile(".facet_group_box")) {
		heading := cascadia.Query(group, cascadia.MustCompile("h2"))
		if heading != nil && strings.Join(strings.Fields(paylocityText(heading, "")), " ") == "Business Unit" {
			groups = append(groups, group)
		}
	}
	if len(groups) != 1 {
		return nil, ErrInventory
	}
	facets := []peopleSoftFacet{}
	names, actions := map[string]bool{}, map[string]bool{}
	total := 0
	for _, node := range cascadia.QueryAll(groups[0], cascadia.MustCompile("input[type=checkbox][id^=PTS_SELECT]")) {
		action, _ := lastNodeAttribute(node, "id")
		label, _ := lastNodeAttribute(node, "aria-label")
		_, checked := lastNodeAttribute(node, "checked")
		match := peopleSoftFacetLabel.FindStringSubmatch(label)
		if len(match) != 3 || !peopleSoftFacetAction.MatchString(action) || checked || names[match[1]] || actions[action] {
			return nil, ErrInventory
		}
		count, e := strconv.Atoi(match[2])
		if e != nil || count < 1 || count >= 500 {
			return nil, ErrInventory
		}
		facets = append(facets, peopleSoftFacet{Name: match[1], Action: action, Count: count})
		names[match[1]], actions[action] = true, true
		total += count
	}
	if len(facets) == 0 || len(facets) > 64 || total != expected {
		return nil, ErrInventory
	}
	return facets, nil
}

func peopleSoftFacetForm(source, action string) (string, error) {
	if !peopleSoftFacetAction.MatchString(action) {
		return "", ErrInventory
	}
	doc, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return "", e
	}
	selected := cascadia.Query(doc, cascadia.MustCompile("input[id='"+action+"']"))
	if selected == nil {
		return "", ErrInventory
	}
	if _, checked := lastNodeAttribute(selected, "checked"); checked {
		return "", ErrInventory
	}
	fields := [][2]string{}
	for _, n := range cascadia.QueryAll(doc, cascadia.MustCompile("form input[name]")) {
		kind, _ := lastNodeAttribute(n, "type")
		if kind == "" {
			kind = "text"
		}
		kind = strings.ToLower(kind)
		_, disabled := lastNodeAttribute(n, "disabled")
		_, checked := lastNodeAttribute(n, "checked")
		if disabled || kind == "button" || kind == "file" || kind == "image" || kind == "reset" || kind == "submit" || (kind == "checkbox" || kind == "radio") && !checked {
			continue
		}
		name, _ := lastNodeAttribute(n, "name")
		if name == "" || name == "ICAction" || name == "ICFocus" || name == "ICXPos" || name == "ICYPos" {
			continue
		}
		value, _ := lastNodeAttribute(n, "value")
		fields = append(fields, [2]string{name, value})
	}
	fields = append(fields, [2]string{action, "Y"}, [2]string{"ICAction", action}, [2]string{"ICFocus", action}, [2]string{"ICXPos", "0"}, [2]string{"ICYPos", "0"})
	return lastQuery(fields), nil
}

func peopleSoftGrid(ctx context.Context, o LastHTTPOptions, fetch SmallProviderFetch, body []byte, allowIdenticalDuplicates bool) ([]Job, error) {
	previous := -1
	previousUnique := -1
	for i := 0; i < 1001; i++ {
		if len(body) == 0 || utf8.RuneCount(body) > 10_000_000 {
			return nil, ErrInventory
		}
		jobs, expected, e := parsePeopleSoftListing(string(body), o, allowIdenticalDuplicates)
		if e != nil || expected > 50_000 {
			if e != nil {
				return nil, e
			}
			return nil, ErrInventory
		}
		unique := map[string]bool{}
		for _, job := range jobs {
			id, valid := job.Metadata["job_id"].(string)
			if !valid {
				return nil, ErrInventory
			}
			unique[id] = true
		}
		if allowIdenticalDuplicates && len(unique) <= previousUnique {
			return nil, ErrInventory
		}
		if len(jobs) == expected {
			return jobs, nil
		}
		if len(jobs) <= previous || len(jobs) > expected {
			return nil, ErrInventory
		}
		previous = len(jobs)
		previousUnique = len(unique)
		form, available, e := PeopleSoftContinuation(string(body))
		if e != nil || !available {
			return nil, ErrInventory
		}
		body, e = fetch(ctx, Request{Method: "POST", URL: o.ListingURL(), Body: form, Headers: http.Header{"Content-Type": {"application/x-www-form-urlencoded"}}})
		if e != nil {
			return nil, e
		}
	}
	return nil, ErrInventory
}

func discoverPeopleSoftBusinessUnits(ctx context.Context, o LastHTTPOptions, fetch SmallProviderFetch, first []byte) ([]Job, error) {
	if len(first) == 0 || utf8.RuneCount(first) > 10_000_000 {
		return nil, ErrInventory
	}
	_, systemTotal, e := ParsePeopleSoftListing(string(first), o)
	if e != nil || systemTotal > 50_000 {
		return nil, ErrInventory
	}
	facets, e := peopleSoftBusinessUnitFacets(string(first), systemTotal)
	if e != nil {
		return nil, e
	}
	bootstrap := o.Origin + "/psp/" + o.Site + "/" + o.Portal + "/HRMS/?cmd=logout"
	seen := map[string]Job{}
	unitByID := map[string]string{}
	duplicateIDs := map[string]bool{}
	ordered := []Job{}
	rawTotal, duplicates := 0, 0
	for _, original := range facets {
		if _, e = fetch(ctx, Request{Method: "GET", URL: bootstrap}); e != nil {
			return nil, e
		}
		page, e := fetch(ctx, Request{Method: "GET", URL: o.ListingURL()})
		if e != nil {
			return nil, e
		}
		if len(page) == 0 || utf8.RuneCount(page) > 10_000_000 {
			return nil, ErrInventory
		}
		_, currentTotal, e := ParsePeopleSoftListing(string(page), o)
		if e != nil || currentTotal != systemTotal {
			return nil, ErrInventory
		}
		current, e := peopleSoftBusinessUnitFacets(string(page), currentTotal)
		if e != nil || len(current) != len(facets) {
			return nil, ErrInventory
		}
		counts := map[string]int{}
		for _, facet := range current {
			counts[facet.Name] = facet.Count
		}
		for _, facet := range facets {
			if counts[facet.Name] != facet.Count {
				return nil, ErrInventory
			}
		}
		var selected peopleSoftFacet
		for _, facet := range current {
			if facet.Name == original.Name {
				selected = facet
				break
			}
		}
		form, e := peopleSoftFacetForm(string(page), selected.Action)
		if e != nil {
			return nil, e
		}
		page, e = fetch(ctx, Request{Method: "POST", URL: o.ListingURL(), Body: form, Headers: http.Header{"Content-Type": {"application/x-www-form-urlencoded"}}})
		if e != nil {
			return nil, e
		}
		jobs, e := peopleSoftGrid(ctx, o, fetch, page, true)
		if e != nil || len(jobs) != selected.Count {
			return nil, ErrInventory
		}
		rawTotal += len(jobs)
		for _, job := range jobs {
			id, ok := job.Metadata["job_id"].(string)
			unit, unitOK := job.Metadata["business_unit"].(string)
			if !ok || !unitOK || unit != selected.Name {
				return nil, ErrInventory
			}
			if prior, exists := seen[id]; exists {
				if unitByID[id] != selected.Name || !reflect.DeepEqual(prior, job) {
					return nil, ErrInventory
				}
				if !o.AllowedDuplicateIDs[id] || duplicateIDs[id] {
					return nil, ErrInventory
				}
				duplicateIDs[id] = true
				duplicates++
				continue
			}
			seen[id], unitByID[id] = job, selected.Name
			ordered = append(ordered, job)
		}
	}
	if rawTotal != systemTotal || len(ordered)+duplicates != systemTotal {
		return nil, ErrInventory
	}
	return ordered, nil
}

// Fetch owns the isolated anonymous cookie jar, bootstrap redirects and original
// GET retry/publisher-policy contract. Continuation POSTs never follow redirects.
func DiscoverPeopleSoft(ctx context.Context, o LastHTTPOptions, fetch SmallProviderFetch) ([]Job, error) {
	if o.Provider != "peoplesoft" || fetch == nil {
		return nil, ErrInventory
	}
	bootstrap := o.Origin + "/psp/" + o.Site + "/" + o.Portal + "/HRMS/?cmd=logout"
	if _, e := fetch(ctx, Request{Method: "GET", URL: bootstrap}); e != nil {
		return nil, e
	}
	body, e := fetch(ctx, Request{Method: "GET", URL: o.ListingURL()})
	if e != nil {
		return nil, e
	}
	if o.PartitionByBusinessUnit {
		return discoverPeopleSoftBusinessUnits(ctx, o, fetch, body)
	}
	return peopleSoftGrid(ctx, o, fetch, body, false)
}
