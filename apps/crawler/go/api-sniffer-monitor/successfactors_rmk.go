package apisniffer

import (
	"context"
	"encoding/json"
	"html"
	"net/http"
	"net/url"
	"reflect"
	"regexp"
	"strings"
	"unicode/utf8"
)

var rmkBrand = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`)
var rmkLocale = regexp.MustCompile(`^[a-z]{2}_[A-Z]{2}$`)
var rmkCSRF = regexp.MustCompile(`["']X-CSRF-Token["']\s*:\s*["']([^"']+)["']`)
var rmkPageBrand = regexp.MustCompile(`\bbrand\s*:\s*['"]([^'"]+)['"]`)
var rmkPageLocale = regexp.MustCompile(`\blocale\s*:\s*['"]([^'"]+)['"]`)

type SuccessFactorsRMKOptions struct{ BoardURL, Origin, Endpoint, Brand, Locale string }

func SuccessFactorsRMKOptionsFromMetadata(board, metadata string) (SuccessFactorsRMKOptions, error) {
	o := SuccessFactorsRMKOptions{BoardURL: board}
	d, e := Decode([]byte(metadata))
	if e != nil {
		return o, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return o, ErrOptions
	}
	if m["preset"] != "successfactors" || m["variant"] != "rmk" {
		return o, ErrOptions
	}
	o.Brand, ok = m["brand"].(string)
	if !ok || !rmkBrand.MatchString(o.Brand) {
		return o, ErrOptions
	}
	if v := m["locale"]; v != nil {
		o.Locale, ok = v.(string)
		if !ok || !rmkLocale.MatchString(o.Locale) {
			return o, ErrOptions
		}
	}
	u, e := url.Parse(board)
	if e != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Opaque != "" || len(board) > 8192 || (u.Port() != "" && u.Port() != "443") || !strings.HasPrefix(u.Path, "/"+o.Brand+"/") {
		return o, ErrOptions
	}
	o.Origin = "https://" + u.Host
	o.Endpoint = o.Origin + "/services/recruiting/v1/jobs"
	return o, nil
}
func (o SuccessFactorsRMKOptions) ResourceMatches(raw string) bool {
	return raw == o.BoardURL || raw == o.Endpoint
}

func (SuccessFactorsRMKOptions) PublisherPolicyOnStatus() bool { return true }

func rmkStrings(raw any) []string {
	values, ok := raw.([]any)
	if !ok || len(values) > 100 {
		return nil
	}
	out := []string{}
	for _, v := range values {
		if s, ok := v.(string); ok && strings.TrimSpace(s) != "" {
			out = append(out, strings.TrimSpace(s))
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}
func rmkJob(raw any, o SuccessFactorsRMKOptions, locale string) (Job, error) {
	job := Job{Metadata: map[string]any{}, Extras: map[string]any{}}
	row, ok := raw.(map[string]any)
	if !ok {
		return job, ErrInventory
	}
	d, ok := row["response"].(map[string]any)
	if !ok || d["brandUrl"] != o.Brand {
		return job, ErrInventory
	}
	id, ok := d["id"].(string)
	if !ok || !smallUnsigned.MatchString(id) {
		return job, ErrInventory
	}
	title, ok := d["unifiedStandardTitle"].(string)
	if !ok || strings.TrimSpace(title) == "" {
		return job, ErrInventory
	}
	rawTitle := d["unifiedUrlTitle"]
	if !detailTruthy(rawTitle) {
		rawTitle = d["urlTitle"]
	}
	urlTitle, ok := rawTitle.(string)
	if !ok || strings.TrimSpace(urlTitle) == "" {
		return job, ErrInventory
	}
	escaped := strings.ReplaceAll(url.QueryEscape(html.UnescapeString(strings.TrimSpace(urlTitle))), "+", "%20")
	escaped = strings.ReplaceAll(escaped, "%25", "%")
	job.URL = o.Origin + "/" + o.Brand + "/job/" + escaped + "/" + id + "-" + locale + "/"
	job.Title = strings.TrimSpace(title)
	job.Locations = rmkStrings(d["filter1"])
	if values := rmkStrings(d["filter4"]); len(values) > 0 {
		job.EmploymentType = values[0]
	}
	if date, ok := d["unifiedStandardStart"].(string); ok && strings.TrimSpace(date) != "" {
		job.DatePosted = strings.TrimSpace(date)
	}
	job.Metadata["id"], job.Metadata["brand"] = id, o.Brand
	for key, source := range map[string]string{"job_function": "filter2", "business_unit": "businessUnit_obj", "division": "division_obj", "currency": "currency"} {
		if values := rmkStrings(d[source]); len(values) > 0 {
			generic := make([]any, len(values))
			for i, value := range values {
				generic[i] = value
			}
			job.Metadata[key] = generic
		}
	}
	return job, nil
}

// Preserve the original bootstrap, stable advertised count, ten-row pages and
// duplicate handling. Conflicting duplicate identities fail the whole run;
// identical repeated rows retain their first value and suppress disappearance.
func DiscoverSuccessFactorsRMK(ctx context.Context, o SuccessFactorsRMKOptions, fetch SmallProviderFetch) (Inventory, error) {
	out := Inventory{Jobs: []Job{}}
	binding, _ := json.Marshal(map[string]any{"preset": "successfactors", "variant": "rmk", "brand": o.Brand, "locale": o.Locale})
	if o.Locale == "" {
		binding, _ = json.Marshal(map[string]any{"preset": "successfactors", "variant": "rmk", "brand": o.Brand})
	}
	bound, bindingErr := SuccessFactorsRMKOptionsFromMetadata(o.BoardURL, string(binding))
	if fetch == nil || bindingErr != nil || bound != o {
		return out, ErrOptions
	}
	page, e := fetch(ctx, Request{Method: "GET", URL: o.BoardURL})
	if e != nil {
		return out, e
	}
	if len(page) > 2_000_000 || !utf8.Valid(page) {
		return out, ErrInventory
	}
	csrf, brand, localeMatch := rmkCSRF.FindSubmatch(page), rmkPageBrand.FindSubmatch(page), rmkPageLocale.FindSubmatch(page)
	if len(csrf) != 2 || len(brand) != 2 || len(localeMatch) != 2 || string(brand[1]) != o.Brand || len(csrf[1]) > 2048 {
		return out, ErrInventory
	}
	for _, b := range csrf[1] {
		if b < 0x21 || b > 0x7e {
			return out, ErrInventory
		}
	}
	locale := o.Locale
	if locale == "" {
		locale = string(localeMatch[1])
	}
	if !rmkLocale.MatchString(locale) {
		return out, ErrInventory
	}
	headers := http.Header{"Content-Type": []string{"application/json"}, "Referer": []string{o.BoardURL}, "X-Csrf-Token": []string{string(csrf[1])}}
	expected, processed := -1, 0
	seen := map[string]int{}
	for number := 0; expected < 0 || processed < expected; number++ {
		if e := ctx.Err(); e != nil {
			return Inventory{}, e
		}
		requestBody := struct {
			Keywords   string `json:"keywords"`
			Locale     string `json:"locale"`
			Location   string `json:"location"`
			PageNumber int    `json:"pageNumber"`
			SortBy     string `json:"sortBy"`
			Brand      string `json:"brand"`
		}{Locale: locale, PageNumber: number, SortBy: "recent", Brand: o.Brand}
		body, _ := json.Marshal(requestBody)
		raw, e := fetch(ctx, Request{Method: "POST", URL: o.Endpoint, Body: string(body), Headers: headers.Clone()})
		if e != nil {
			return Inventory{}, e
		}
		if len(raw) > 5_000_000 {
			return Inventory{}, ErrInventory
		}
		d, e := Decode(raw)
		if e != nil {
			return Inventory{}, ErrInventory
		}
		m, ok := d.Value.(map[string]any)
		if !ok {
			return Inventory{}, ErrInventory
		}
		n, ok := m["totalJobs"].(json.Number)
		if !ok {
			return Inventory{}, ErrInventory
		}
		total, e := n.Int64()
		if e != nil || total < 0 || total > 50000 {
			return Inventory{}, ErrInventory
		}
		rows, ok := m["jobSearchResult"].([]any)
		if !ok || len(rows) > 10 {
			return Inventory{}, ErrInventory
		}
		if expected < 0 {
			expected = int(total)
		} else if int(total) != expected {
			return Inventory{}, ErrInventory
		}
		if len(rows) != min(10, expected-processed) {
			return Inventory{}, ErrInventory
		}
		processed += len(rows)
		for _, row := range rows {
			job, e := rmkJob(row, o, locale)
			if e != nil {
				return Inventory{}, e
			}
			id := job.Metadata["id"].(string)
			if index, exists := seen[id]; exists {
				if !reflect.DeepEqual(out.Jobs[index], job) {
					return Inventory{}, ErrInventory
				}
				out.Truncated = true
				continue
			}
			seen[id] = len(out.Jobs)
			out.Jobs = append(out.Jobs, job)
		}
	}
	if expected < 0 || processed != expected {
		return Inventory{}, ErrInventory
	}
	return out, nil
}
