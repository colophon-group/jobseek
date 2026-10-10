package apisniffer

import (
	"context"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	xhtml "golang.org/x/net/html"
)

var peopleSoftCount = regexp.MustCompile(`(?i)<b>\s*([0-9]{1,6})\s*</b>\s*jobs?\s+found`)

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

func ParsePeopleSoftListing(source string, o LastHTTPOptions) ([]Job, int, error) {
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
	seen := map[string]bool{}
	for _, row := range cascadia.QueryAll(doc, cascadia.MustCompile(`li[id^='HRS_AGNT_RSLT_I$0_row_']`)) {
		id, title, location := peopleSoftValue(row, "HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID"), peopleSoftValue(row, "SCH_JOB_TITLE"), peopleSoftValue(row, "LOCATION")
		if !peopleSoftID.MatchString(id) || title == "" || location == "" || seen[id] {
			return nil, 0, ErrInventory
		}
		seen[id] = true
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
		jobs = append(jobs, Job{URL: o.PeopleSoftJobURL(id), Title: title, Locations: []string{location}, DatePosted: date, Metadata: metadata})
	}
	return jobs, expected, nil
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
	previous := -1
	for i := 0; i < 1001; i++ {
		if len(body) == 0 || utf8.RuneCount(body) > 10_000_000 {
			return nil, ErrInventory
		}
		jobs, expected, e := ParsePeopleSoftListing(string(body), o)
		if e != nil || expected > 50_000 {
			if e != nil {
				return nil, e
			}
			return nil, ErrInventory
		}
		if len(jobs) == expected {
			return jobs, nil
		}
		if len(jobs) <= previous || len(jobs) > expected {
			return nil, ErrInventory
		}
		previous = len(jobs)
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
