package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"unicode"
)

var nowHiringSlug = regexp.MustCompile(`(?i)^[a-z0-9][a-z0-9_-]{0,127}$`)
var nowHiringID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var nowHiringInteger = regexp.MustCompile(`^-?[0-9]+$`)

func nowHiringAccountDigits(value string) bool {
	if value == "" {
		return false
	}
	for _, r := range value {
		if !unicode.IsDigit(r) {
			return false
		}
	}
	return true
}

func NowHiringSlugFromURL(source string) (string, error) {
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || u.User != nil || !strings.EqualFold(u.Hostname(), "nowhiring.com") || u.Port() != "" && u.Port() != "443" {
		return "", ErrOptions
	}
	parts := strings.FieldsFunc(u.Path, func(r rune) bool { return r == '/' })
	if len(parts) != 1 || !nowHiringSlug.MatchString(parts[0]) {
		return "", ErrOptions
	}
	return strings.ToLower(parts[0]), nil
}

func NowHiringCriteria(d *Document, value any) map[string][]string {
	out := map[string][]string{}
	items, _ := value.([]any)
	for _, value := range items {
		row, ok := value.(map[string]any)
		if !ok {
			continue
		}
		name, ok := row["fieldName"].(string)
		if !ok {
			continue
		}
		switch value := row["fieldValue"].(type) {
		case string, bool:
		case json.Number:
			if !nowHiringInteger.MatchString(string(value)) {
				continue
			}
		default:
			continue
		}
		text, err := d.String(row["fieldValue"])
		if err == nil && strings.TrimSpace(text) != "" {
			out[name] = append(out[name], strings.TrimSpace(text))
		}
	}
	return out
}

func NowHiringSearchPayload(values map[string][]string, start int) map[string]any {
	copyValues := func(key string) []string { return append([]string{}, values[key]...) }
	return map[string]any{
		"customerId": copyValues("billingAccountId"), "brandTemplateId": copyValues("brandTemplateId"), "brandId": copyValues("brandId"),
		"start": strconv.Itoa(start), "sort": "jobtitle", "includefacetlist": false,
		"manyselectfacets": []any{}, "specificfacets": []any{}, "disablesuppression": true, "specificlocationsonly": true,
	}
}

func NowHiringJobFields(d *Document, row map[string]any, slug, customer string) (map[string]any, error) {
	idValue, present := row["id"]
	if !present {
		idValue = ""
	}
	id, err := d.String(idValue)
	id = strings.TrimSpace(id)
	accountValue := row["billingAccountId"]
	if !detailTruthy(accountValue) {
		accountValue = customer
	}
	account, accountErr := d.String(accountValue)
	account = strings.TrimSpace(account)
	title := smallText(row["jobTitle"])
	if err != nil || accountErr != nil || !nowHiringID.MatchString(id) || !nowHiringAccountDigits(account) || title == "" {
		return nil, nil
	}
	if !explicitNextdataIdentity.MatchString("nowhiring:" + account + ":" + id) {
		return nil, ErrInventory
	}
	fields := map[string]any{"url": "https://nowhiring.com/" + slug + "/job-details/" + id, "title": title, "language": "en", "source_identity": "nowhiring:" + account + ":" + id}
	for key, target := range map[string]string{"jobDescription": "description", "postedDate": "date_posted"} {
		if text, ok := row[key].(string); ok {
			fields[target] = text
		}
	}
	parts := []string{}
	seen := map[string]bool{}
	for _, key := range []string{"addressLine1", "addressLine2", "city", "stateProvCode", "postalCode"} {
		text := smallText(row[key])
		if text != "" && !seen[text] {
			seen[text] = true
			parts = append(parts, text)
		}
	}
	if len(parts) > 0 {
		fields["locations"] = []string{strings.Join(parts, ", ")}
	}
	if categories, ok := row["categories"].([]any); ok {
		for _, value := range categories {
			if text := smallText(value); text != "" {
				fields["employment_type"] = text
				break
			}
		}
	}
	application := row["thirdPartyApplyUrl"]
	if !detailTruthy(application) {
		application = row["applicationURL"]
	}
	metadata := map[string]any{}
	for key, value := range map[string]any{"job_id": row["id"], "company": row["company"], "application_url": application, "brand_id": row["brandId"]} {
		switch value.(type) {
		case []any, map[string]any:
			return nil, ErrInventory // The original set-membership check rejects unhashable values.
		}
		if value != nil && value != "" {
			metadata[key] = value
		}
	}
	if len(metadata) > 0 {
		fields["metadata"] = metadata
	}
	return fields, nil
}

func DiscoverNowHiring(ctx context.Context, slug string, fetch SmallProviderFetch) ([]map[string]any, bool, error) {
	if ctx == nil || fetch == nil || !nowHiringSlug.MatchString(slug) {
		return nil, false, ErrOptions
	}
	slug = strings.ToLower(slug)
	decode := func(ctx context.Context, request Request) (*Document, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		body, err := fetch(ctx, request)
		if err != nil {
			return nil, err
		}
		return Decode(body)
	}
	headers := http.Header{"Accept": []string{"application/json"}, "Referer": []string{"https://nowhiring.com/" + slug + "/"}}
	site, err := decode(ctx, Request{Method: "GET", URL: "https://nowhiring.com/api/career-live-sites/" + url.PathEscape("nowhiring.com/"+slug), Headers: headers})
	if err != nil {
		return nil, false, err
	}
	root, ok := site.Value.(map[string]any)
	if !ok {
		return nil, false, ErrInventory
	}
	criteria := NowHiringCriteria(site, root["jobSearchCriteria"])
	if len(criteria["billingAccountId"]) == 0 {
		return nil, false, ErrInventory
	}
	headers.Set("Referer", "https://nowhiring.com/")
	summaries := []map[string]any{}
	total := 0
	for len(summaries) < 50000 {
		payload, err := json.Marshal(NowHiringSearchPayload(criteria, len(summaries)))
		if err != nil {
			return nil, false, err
		}
		postHeaders := headers.Clone()
		postHeaders.Set("Content-Type", "application/json")
		d, err := decode(ctx, Request{Method: "POST", URL: "https://nowhiring.com/api/jobs/search", Body: string(payload), Headers: postHeaders})
		if err != nil {
			return nil, false, err
		}
		root, ok := d.Value.(map[string]any)
		if !ok {
			return nil, false, ErrInventory
		}
		rows, ok := root["list"].([]any)
		value := root["total"]
		if flag, ok := value.(bool); ok {
			value = json.Number("0")
			if flag {
				value = json.Number("1")
			}
		}
		total, err = smallInt(value, false)
		if !ok || err != nil {
			return nil, false, ErrInventory
		}
		count := 0
		for _, value := range rows {
			if row, ok := value.(map[string]any); ok {
				count++
				summaries = append(summaries, row)
			}
		}
		if len(summaries) >= total {
			break
		}
		if count == 0 {
			return nil, false, ErrInventory
		}
	}
	ids := []string{}
	seen := map[string]bool{}
	truncated := total > len(summaries)
	for _, row := range summaries {
		value, present := row["id"]
		if !present {
			value = ""
		}
		id, err := site.String(value)
		id = strings.TrimSpace(id)
		if err != nil || !nowHiringID.MatchString(id) {
			return nil, false, ErrInventory
		}
		if seen[id] {
			truncated = true
			continue
		}
		seen[id] = true
		ids = append(ids, id)
	}
	child, cancel := context.WithCancel(ctx)
	defer cancel()
	jobs := make([]map[string]any, len(ids))
	var first error
	var mu sync.Mutex
	var wg sync.WaitGroup
	sem := make(chan struct{}, 10)
	for index, id := range ids {
		wg.Add(1)
		go func(index int, id string) {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-child.Done():
				return
			}
			d, err := decode(child, Request{Method: "GET", URL: "https://nowhiring.com/api/jobs/" + id, Headers: headers})
			if err == nil {
				if row, ok := d.Value.(map[string]any); ok {
					jobs[index], err = NowHiringJobFields(d, row, slug, criteria["billingAccountId"][0])
				} else {
					err = ErrInventory
				}
			}
			if err != nil {
				mu.Lock()
				if first == nil {
					first = err
				}
				mu.Unlock()
				cancel()
			}
		}(index, id)
	}
	wg.Wait()
	if first != nil {
		return nil, false, first
	}
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	valid := []map[string]any{}
	for _, job := range jobs {
		if job == nil {
			truncated = true
		} else {
			valid = append(valid, job)
		}
	}
	if len(ids) > 0 && len(valid) == 0 {
		return nil, false, ErrInventory
	}
	return valid, truncated, nil
}
