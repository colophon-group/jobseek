package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"regexp"
	"strings"
)

var ErrTalentReefMetadata = errors.New("TalentReef posting metadata is not a scalar")

type TalentReefScope struct {
	Alias, ClientID, Locale string
	Brands                  []string
}

func ParseTalentReefScope(d *Document, o TenthProviderOptions) (TalentReefScope, error) {
	s := TalentReefScope{Alias: o.Alias, Locale: o.Locale}
	rows, ok := d.Value.([]any)
	if !ok {
		return s, ErrInventory
	}
	var published []map[string]any
	for _, raw := range rows {
		if m, ok := raw.(map[string]any); ok && detailTruthy(m["published"]) {
			published = append(published, m)
		}
	}
	if len(published) == 0 {
		return s, ErrInventory
	}
	selected := published[0]
	for _, m := range published {
		if detailTruthy(m["defaultLocale"]) {
			selected = m
			break
		}
	}
	for _, m := range published {
		if strings.ToLower(tenthPythonString(d, m["locale"])) == o.Locale {
			selected = m
			break
		}
	}
	if v, exists := selected["clientId"]; exists {
		s.ClientID = strings.TrimSpace(tenthPythonString(d, v))
	}
	if v := selected["locale"]; detailTruthy(v) {
		s.Locale = strings.ToLower(tenthPythonString(d, v))
	}
	if !regexp.MustCompile(`^[0-9]{1,20}$`).MatchString(s.ClientID) || !tenthLocale.MatchString(s.Locale) {
		return s, ErrInventory
	}
	if brands, ok := selected["brands"].([]any); ok {
		for _, raw := range brands {
			if text, ok := raw.(string); ok && strings.TrimSpace(text) != "" {
				s.Brands = append(s.Brands, strings.TrimSpace(text))
			}
		}
	}
	if len(s.Brands) == 0 || len(s.Brands) > 1000 {
		return s, ErrInventory
	}
	return s, nil
}

func TalentReefSearchRequest(s TalentReefScope, start int) (Request, error) {
	locale := s.Locale
	if locale == "en" {
		locale = "en-us"
	}
	payload := map[string]any{
		"from": start, "size": 1000,
		"_source": []string{"positionType", "category", "description", "address", "jobId", "clientId", "clientName", "brandId", "brand", "internalOrExternal", "url", "postingUuid", "isSalaried", "minCompensation", "maxCompensation", "pubCompensation", "createdDate"},
		"query": map[string]any{"bool": map[string]any{"filter": []any{
			map[string]any{"terms": map[string]any{"clientId.raw": []string{s.ClientID}}},
			map[string]any{"terms": map[string]any{"brand.raw": s.Brands}},
			map[string]any{"terms": map[string]any{"internalOrExternal": []any{map[string]any{"internalOrExternal": "externalOnly"}}}},
		}}},
		"sort": []any{map[string]any{"positionType.raw": map[string]any{"order": "asc"}}},
	}
	body, err := json.Marshal(payload)
	return Request{Method: "POST", URL: "https://prod-kong.internal.talentreef.com/apply/proxy-es/search-" + locale + "/posting/_search", Body: string(body), Headers: http.Header{"Accept": {"application/json"}, "Content-Type": {"application/json"}, "Referer": {"https://apply.jobappnetwork.com/"}}}, err
}

func ParseTalentReefPage(d *Document) ([]map[string]any, int, error) {
	root, ok := d.Value.(map[string]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	hits, ok := root["hits"].(map[string]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	raw, ok := hits["hits"].([]any)
	if !ok || len(raw) > 1000 {
		return nil, 0, ErrInventory
	}
	v, exists := hits["total"]
	if !exists {
		v = json.Number("0")
	}
	if m, ok := v.(map[string]any); ok {
		v, exists = m["value"]
		if !exists {
			v = json.Number("0")
		}
	}
	// Python's int check accepts booleans here. Preserve that small total contract.
	if b, ok := v.(bool); ok {
		if b {
			v = json.Number("1")
		} else {
			v = json.Number("0")
		}
	}
	if _, ok := v.(json.Number); !ok {
		return nil, 0, ErrInventory
	}
	total, err := tenthNonnegative(v)
	if err != nil {
		return nil, 0, err
	}
	rows := []map[string]any{}
	for _, value := range raw {
		if m, ok := value.(map[string]any); ok {
			rows = append(rows, m)
		}
	}
	return rows, total, nil
}

func TalentReefJobFields(d *Document, hit map[string]any, s TalentReefScope) (map[string]any, error) {
	m, ok := hit["_source"].(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	jobID := ""
	if v, exists := m["jobId"]; exists {
		jobID = strings.TrimSpace(tenthPythonString(d, v))
	}
	title, ok := m["positionType"].(string)
	if !ok || strings.TrimSpace(title) == "" || jobID == "" {
		return nil, ErrInventory
	}
	f := map[string]any{"url": "https://apply.jobappnetwork.com/clients/" + s.ClientID + "/posting/" + jobID + "/" + s.Locale, "title": strings.TrimSpace(title), "language": strings.SplitN(s.Locale, "-", 2)[0], "source_identity": "talentreef:" + s.ClientID + ":" + jobID}
	for source, target := range map[string]string{"description": "description", "category": "employment_type", "createdDate": "date_posted"} {
		if v, ok := m[source].(string); ok {
			f[target] = v
		}
	}
	parts := []string{}
	if a, ok := m["address"].(map[string]any); ok {
		for _, key := range []string{"street1", "city", "stateOrProvince", "postalCode", "country"} {
			if v, ok := a[key].(string); ok && strings.TrimSpace(v) != "" {
				v = strings.TrimSpace(v)
				seen := false
				for _, part := range parts {
					if part == v {
						seen = true
						break
					}
				}
				if !seen {
					parts = append(parts, v)
				}
			}
		}
	}
	if len(parts) > 0 {
		f["locations"] = []string{strings.Join(parts, ", ")}
	}
	metadata := map[string]any{}
	for source, target := range map[string]string{"jobId": "job_id", "postingUuid": "posting_uuid", "brand": "brand", "clientName": "client_name"} {
		if v, exists := m[source]; exists && v != nil {
			switch v.(type) {
			case map[string]any, []any:
				return nil, ErrTalentReefMetadata
			}
			if text, ok := v.(string); !ok || text != "" {
				metadata[target] = v
			}
		}
	}
	if len(metadata) > 0 {
		f["metadata"] = metadata
	}
	return f, nil
}

func DiscoverTalentReef(ctx context.Context, o TenthProviderOptions, fetch TenthProviderFetch) ([]map[string]any, bool, error) {
	body, err := fetch(ctx, Request{Method: "GET", URL: o.ListingURL(), Headers: http.Header{"Accept": {"application/json"}, "Referer": {"https://apply.jobappnetwork.com/" + o.Alias}}})
	if err != nil {
		return nil, false, err
	}
	d, err := Decode(body)
	if err != nil {
		return nil, false, err
	}
	s, err := ParseTalentReefScope(d, o)
	if err != nil {
		return nil, false, err
	}
	type locatedHit struct {
		document *Document
		hit      map[string]any
	}
	hits := []locatedHit{}
	total := 0
	for requests := 0; len(hits) < 50000; requests++ {
		if requests >= 1000 {
			return nil, false, ErrInventory
		}
		req, err := TalentReefSearchRequest(s, len(hits))
		if err != nil {
			return nil, false, err
		}
		body, err := fetch(ctx, req)
		if err != nil {
			return nil, false, err
		}
		d, err := Decode(body)
		if err != nil {
			return nil, false, err
		}
		page, count, err := ParseTalentReefPage(d)
		if err != nil {
			return nil, false, err
		}
		total = count
		for _, hit := range page {
			hits = append(hits, locatedHit{d, hit})
		}
		if len(hits) >= total {
			break
		}
		if len(page) == 0 {
			return nil, false, ErrInventory
		}
	}
	jobs := []map[string]any{}
	seen := map[string]bool{}
	invalid := 0
	for _, hit := range hits {
		fields, err := TalentReefJobFields(hit.document, hit.hit, s)
		if errors.Is(err, ErrTalentReefMetadata) {
			return nil, false, err
		}
		if err != nil {
			invalid++
			continue
		}
		source := fields["url"].(string)
		if seen[source] {
			invalid++
			continue
		}
		seen[source] = true
		jobs = append(jobs, fields)
	}
	if len(hits) > 0 && len(jobs) == 0 {
		return nil, false, ErrInventory
	}
	return jobs, invalid > 0 || total > len(hits), nil
}

func tenthPythonString(d *Document, value any) string {
	text, err := d.String(value)
	if err != nil {
		return ""
	}
	return text
}
