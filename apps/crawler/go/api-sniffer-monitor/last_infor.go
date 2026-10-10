package apisniffer

import (
	"context"
	"net/http"
	"strconv"
	"strings"
	"time"
)

func inforSessionHeaders(headers http.Header) (http.Header, error) {
	cookies := OriginalSessionCookies(headers)
	values := map[string]string{}
	order := []string{}
	for _, c := range cookies {
		if _, exists := values[c.Name]; !exists {
			order = append(order, c.Name)
		}
		values[c.Name] = c.Value
	}
	csrf := values["SSO.CSRF"]
	if csrf == "" {
		return nil, ErrInventory
	}
	pairs := make([]string, 0, len(order))
	for _, k := range order {
		pairs = append(pairs, k+"="+values[k])
	}
	h := http.Header{}
	h.Set("Accept", "application/json")
	h.Set("Cookie", strings.Join(pairs, "; "))
	h.Set("SSO.CSRF", csrf)
	return h, nil
}
func inforDate(raw any) (*string, error) {
	if raw == nil || raw == "" || raw == "00000000" {
		return nil, nil
	}
	s, ok := raw.(string)
	if !ok {
		return nil, ErrField
	}
	if len(s) < 6 || len(s) > 8 {
		return nil, ErrField
	}
	year, e := strconv.Atoi(s[:4])
	if e != nil || year < 1 || year > 9999 {
		return nil, ErrField
	}
	for monthWidth := 2; monthWidth >= 1; monthWidth-- {
		dayWidth := len(s) - 4 - monthWidth
		if dayWidth < 1 || dayWidth > 2 {
			continue
		}
		month, e := strconv.Atoi(s[4 : 4+monthWidth])
		if e != nil || month < 1 || month > 12 {
			continue
		}
		day, e := strconv.Atoi(s[4+monthWidth:])
		if e != nil || day < 1 || day > 31 {
			continue
		}
		date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
		if date.Year() == year && int(date.Month()) == month && date.Day() == day {
			out := date.Format("2006-01-02")
			return &out, nil
		}
	}

	return nil, ErrField
}
func inforLocation(raw any) []string {
	s, ok := raw.(string)
	if !ok {
		return nil
	}
	parts := []string{}
	for _, v := range strings.Split(s, ":") {
		p := strings.TrimFunc(v, enterpriseSpace)
		if p != "" {
			parts = append(parts, p)
		}
	}
	if len(parts) == 0 {
		return nil
	}
	for left, right := 0, len(parts)-1; left < right; left, right = left+1, right-1 {
		parts[left], parts[right] = parts[right], parts[left]
	}
	return []string{strings.Join(parts, ", ")}
}
func ParseInforListing(body []byte, o LastHTTPOptions) ([]Job, error) {
	document, e := Decode(body)
	if e != nil {
		return nil, e
	}
	root, ok := document.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	rows, ok := root["JobPostingListWebServices_ListOperationResponseArray"].([]any)
	if !ok || len(rows) > 50_000 {
		return nil, ErrInventory
	}
	out := make([]Job, 0, len(rows))
	seen := map[string]bool{}
	for _, raw := range rows {
		wrapper, ok := raw.(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		row, ok := wrapper["JobPostingListWebServices_ListOperationResponse"].(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		req, post, title := lastPythonScalar(document, row["JobRequisition"]), lastPythonScalar(document, row["JobPosting"]), lastPythonScalar(document, row["__Description_translation___"])
		key := req + "\x00" + post
		if !inforToken.MatchString(req) || !inforToken.MatchString(post) || title == "" || seen[key] {
			return nil, ErrInventory
		}
		seen[key] = true
		date, e := inforDate(row["PostingDateRange.Begin"])
		if e != nil {
			return nil, e
		}
		metadata := map[string]any{"job_requisition": req, "job_posting": post}
		for source, target := range map[string]string{"WorkType": "work_type", "Category": "category", "SubCategory": "subcategory"} {
			if v, ok := row[source].(string); ok && strings.TrimFunc(v, enterpriseSpace) != "" {
				metadata[target] = strings.TrimFunc(v, enterpriseSpace)
			}
		}
		var posted any
		if date != nil {
			posted = *date
		}
		out = append(out, Job{URL: o.InforJobURL(req, post), Title: title, Locations: inforLocation(row["LocationOfJob"]), DatePosted: posted, Metadata: metadata, Extras: map[string]any{"language": "en"}})
	}
	return out, nil
}
func DiscoverInfor(ctx context.Context, o LastHTTPOptions, fetch SuccessFactorsLegacyFetch) ([]Job, error) {
	if o.Provider != "infor" || fetch == nil {
		return nil, ErrInventory
	}
	_, headers, e := fetch(ctx, Request{Method: "GET", URL: o.BoardURL})
	if e != nil {
		return nil, e
	}
	session, e := inforSessionHeaders(headers)
	if e != nil {
		return nil, e
	}
	pairs := [][2]string{{"_clientType", "INTERNAL"}, {"JobBoard", o.JobBoard}, {"LocationOfJob", " "}, {"Category", " "}, {"SubCategory", " "}, {"WorkType", " "}, {"JobRequisition", " "}, {"__Description_translation___", " "}, {"JobPosting", " "}, {"PostingStatus", "2"}, {"PostingDateRange.Begin", " "}, {"PostingDateRange.End", " "}, {"JobRequisitionPriority", " "}, {"csk.IsoLocale", "en"}, {"HROrganization", o.HROrganization}, {"_limit", "-1"}, {"AtApplicationLimit", " "}}
	body, _, e := fetch(ctx, Request{Method: "GET", URL: o.ListingURL() + "?" + lastQuery(pairs), Headers: session})
	if e != nil {
		return nil, e
	}
	return ParseInforListing(body, o)
}
