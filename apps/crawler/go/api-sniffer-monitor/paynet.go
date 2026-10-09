package apisniffer

import (
	"context"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const PayNetAPIURL = "https://api.pay-netonline.com/applicantpublic/jobpostings"

var payNetCompany = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_-]{1,31}$`)
var payNetUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

func PayNetCompanyFromURL(source string) (string, error) {
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" ||
		!strings.EqualFold(u.Path, "/paynet/applicant/postings.aspx") ||
		!strings.EqualFold(u.Hostname(), "pay-netonline.com") && !strings.EqualFold(u.Hostname(), "www.pay-netonline.com") {
		return "", ErrOptions
	}
	values, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(values) != 1 {
		return "", ErrOptions
	}
	for key, values := range values {
		if !strings.EqualFold(key, "co") || len(values) != 1 || !payNetCompany.MatchString(strings.TrimSpace(values[0])) {
			return "", ErrOptions
		}
		return strings.TrimSpace(values[0]), nil
	}
	return "", ErrOptions
}

func payNetHTML(value any) string {
	text := smallText(value)
	if text == "" || strings.Contains(text, "<") && strings.Contains(text, ">") {
		return text
	}
	return "<p>" + strings.ReplaceAll(eighthEscape(text), "\n", "<br>") + "</p>"
}

func payNetDate(value any) string {
	text := smallText(value)
	for _, layout := range []string{"1/2/2006", "2006-1-2"} {
		if date, err := time.Parse(layout, text); err == nil && date.Year() >= 1 {
			return date.Format("2006-01-02")
		}
	}
	return ""
}

func PayNetJobFields(value any, company string) (map[string]any, error) {
	row, ok := value.(map[string]any)
	if !ok {
		return nil, nil
	}
	id, ok := row["ID"].(string)
	id = strings.ToLower(id)
	position, validPosition := row["Position"].(map[string]any)
	if !ok || !payNetUUID.MatchString(id) || !validPosition {
		return nil, nil
	}
	title := smallText(position["Title"])
	summary := payNetHTML(position["Description"])
	if summary == "" {
		summary = payNetHTML(position["Text"])
	}
	requirements := payNetHTML(position["Requirements"])
	description := summary
	if requirements != "" {
		if summary != "" {
			description += "\n<h3>Requirements</h3>\n"
		}
		description += requirements
	}
	if title == "" || description == "" {
		return nil, nil
	}
	metadata := map[string]any{"posting_id": id}
	if employer, ok := row["Company"].(map[string]any); ok {
		if name := smallText(employer["Name"]); name != "" {
			metadata["company_name"] = name
		}
	}
	if date := payNetDate(position["EndDate"]); date != "" {
		metadata["valid_through"] = date
	}
	fields := map[string]any{
		"url":             "https://www.pay-netonline.com/PayNet/Applicant/Posting.aspx?JobPostingID=" + id,
		"title":           title,
		"description":     description,
		"metadata":        metadata,
		"source_identity": "paynet:" + strings.ToLower(company) + ":" + id,
	}
	if location := smallText(position["Location"]); location != "" {
		fields["locations"] = []string{location}
	}
	if date := payNetDate(position["StartDate"]); date != "" {
		fields["date_posted"] = date
	}
	if requirements != "" {
		fields["extras"] = map[string]any{"qualifications": requirements}
	}
	return fields, nil
}

func DiscoverPayNet(ctx context.Context, board string, fetch SmallProviderFetch) ([]map[string]any, bool, error) {
	company, err := PayNetCompanyFromURL(board)
	if err != nil || ctx == nil || fetch == nil {
		return nil, false, ErrOptions
	}
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	body, err := fetch(ctx, Request{Method: "GET", URL: PayNetAPIURL + "?company_id=" + url.QueryEscape(company), Headers: http.Header{"Accept": []string{"application/json"}}})
	if err != nil {
		return nil, false, err
	}
	if len(body) > 50_000_000 {
		return nil, false, ErrInventory
	}
	d, err := Decode(body)
	if err != nil {
		return nil, false, ErrInventory
	}
	rows, ok := d.Value.([]any)
	if !ok {
		return nil, false, ErrInventory
	}
	jobs := []map[string]any{}
	seen := map[string]bool{}
	truncated := len(rows) > 50000
	for _, row := range rows[:min(len(rows), 50000)] {
		if err := ctx.Err(); err != nil {
			return nil, false, err
		}
		fields, err := PayNetJobFields(row, company)
		if err != nil {
			return nil, false, err
		}
		if fields == nil {
			truncated = true
			continue
		}
		identity := fields["source_identity"].(string)
		if seen[identity] {
			truncated = true
			continue
		}
		seen[identity] = true
		jobs = append(jobs, fields)
	}
	if len(rows) > 0 && len(jobs) == 0 {
		return nil, false, ErrInventory
	}
	return jobs, truncated, nil
}
