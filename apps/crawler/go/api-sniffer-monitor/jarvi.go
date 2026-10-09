package apisniffer

import (
	"html"
	"math"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

var jarviSDK = regexp.MustCompile(`(?i)data-sdk\s*=\s*(?:"jarvi"|'jarvi')`)
var jarviPublicKey = regexp.MustCompile(`(?i)data-public-api-key\s*=\s*(?:"([^'"]+)"|'([^'"]+)')`)
var jarviCurrency = regexp.MustCompile(`(?i)data-currency\s*=\s*(?:"([A-Za-z]{3})"|'([A-Za-z]{3})')`)
var jarviTags = regexp.MustCompile(`<[^>]+>`)
var jarviLocation = regexp.MustCompile(`(?is)Localisation\s*:\s*(.*?)(?:</p>|<br\s*/?>|(?:\s+-\s+)?Contrat\s*:|Rémunération\s*:|$)`)

// JarviEmbed reads only the public SDK annotation. Transport admission must
// bind its key to the fixed public offers endpoint; it is never a log value.
func JarviEmbed(page string) (map[string]string, error) {
	if len(page) > 5_000_000 || !jarviSDK.MatchString(page) {
		return nil, ErrInventory
	}
	m := jarviPublicKey.FindStringSubmatch(page)
	if m == nil {
		return nil, ErrInventory
	}
	key := m[1]
	if key == "" {
		key = m[2]
	}
	out := map[string]string{"public_api_key": strings.TrimSpace(html.UnescapeString(key))}
	if m = jarviCurrency.FindStringSubmatch(page); m != nil {
		currency := m[1]
		if currency == "" {
			currency = m[2]
		}
		out["currency"] = strings.ToUpper(currency)
	}
	return out, nil
}

func jarviFields(raw map[string]any, purpose string) []map[string]any {
	out := []map[string]any{}
	values, _ := raw["fieldsValues"].([]any)
	for _, v := range values {
		field, ok := v.(map[string]any)
		if !ok {
			continue
		}
		kind, _ := field["field"].(map[string]any)
		if kind["purpose"] == purpose {
			out = append(out, field)
		}
	}
	return out
}
func jarviValue(raw map[string]any, purpose string) string {
	for _, v := range jarviFields(raw, purpose) {
		if text := smallText(v["value"]); text != "" {
			return text
		}
	}
	return ""
}
func jarviText(value any) string {
	s, _ := value.(string)
	return strings.Join(strings.Fields(html.UnescapeString(jarviTags.ReplaceAllString(s, " "))), " ")
}
func jarviNumber(value string) any {
	if value == "" {
		return nil
	}
	n, err := strconv.ParseFloat(strings.ReplaceAll(strings.TrimSpace(value), ",", "."), 64)
	if err != nil || math.IsInf(n, 0) || math.IsNaN(n) {
		return nil
	}
	return n
}

// JarviJobFields preserves the original field purposes and URL slug. A missing
// title or string identity is an omitted row, as in the Python monitor.
func JarviJobFields(raw map[string]any, board, currency string) (map[string]any, error) {
	title := jarviValue(raw, "joboffer_title")
	if title == "" {
		title, _ = raw["name"].(string)
	}
	title = jarviText(title)
	id := raw["shortId"]
	if !detailTruthy(id) {
		id = raw["id"]
	}
	identity := smallText(id)
	if title == "" || identity == "" {
		return nil, nil
	}
	u, err := url.Parse(board)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil {
		return nil, ErrOptions
	}
	slug := norm.NFD.String(cases.Fold().String(title))
	var b strings.Builder
	space := false
	for _, r := range slug {
		if unicode.Is(unicode.Mn, r) {
			continue
		}
		if unicode.IsSpace(r) {
			if !space {
				b.WriteByte('-')
			}
			space = true
			continue
		}
		space = false
		if r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '-' {
			b.WriteRune(r)
		}
	}
	slug = strings.Trim(b.String(), "-")
	if slug != "" {
		identity += "/" + slug
	}
	pairs := []string{}
	for _, part := range strings.Split(u.RawQuery, "&") {
		keyValue := strings.SplitN(part, "=", 2)
		if len(keyValue) != 2 {
			continue
		}
		key, e := url.QueryUnescape(keyValue[0])
		if e != nil {
			return nil, ErrOptions
		}
		value, e := url.QueryUnescape(keyValue[1])
		if e != nil {
			return nil, ErrOptions
		}
		if key != "q" && value != "" {
			pairs = append(pairs, url.QueryEscape(key)+"="+url.QueryEscape(value))
		}
	}
	pairs = append(pairs, "q="+url.QueryEscape(identity))
	u.RawQuery = strings.Join(pairs, "&")
	u.Fragment = ""
	out := map[string]any{"url": u.String(), "title": title}
	parts := []string{}
	extras := map[string]any{}
	for _, purpose := range []string{"joboffer_company_description", "joboffer_description", "joboffer_profile_description"} {
		if value := jarviValue(raw, purpose); value != "" {
			parts = append(parts, value)
			if purpose == "joboffer_description" {
				extras["responsibilities"] = value
			}
			if purpose == "joboffer_profile_description" {
				extras["qualifications"] = value
			}
		}
	}
	if len(parts) > 0 {
		out["description"] = strings.Join(parts, "\n")
	}
	if len(extras) > 0 {
		out["extras"] = extras
	}
	locations := []string{}
	for _, field := range jarviFields(raw, "joboffer_location") {
		location, _ := field["location"].(map[string]any)
		for _, key := range []string{"formattedAddress", "search", "locality"} {
			if name := smallText(location[key]); name != "" {
				found := false
				for _, prior := range locations {
					if rawName, _ := location[key].(string); prior == rawName {
						found = true
					}
				}
				if !found {
					locations = append(locations, name)
				}
				break
			}
		}
	}
	if len(locations) == 0 {
		for _, purpose := range []string{"joboffer_profile_description", "joboffer_description"} {
			text := strings.ReplaceAll(html.UnescapeString(jarviValue(raw, purpose)), "&nbsp;", " ")
			if m := jarviLocation.FindStringSubmatch(text); m != nil {
				if value := jarviText(m[1]); value != "" {
					locations = append(locations, strings.Trim(value, " -"))
					break
				}
			}
		}
	}
	if len(locations) > 0 {
		out["locations"] = locations
	}
	for _, field := range jarviFields(raw, "joboffer_contract_type") {
		choice, _ := field["fieldValue"].(map[string]any)
		for _, key := range []string{"technicalValue", "name"} {
			if value := smallText(choice[key]); value != "" {
				out["employment_type"] = value
				break
			}
		}
		if out["employment_type"] != nil {
			break
		}
	}
	if out["employment_type"] == nil && cases.Fold().String(jarviValue(raw, "joboffer_is_fulltime")) == "true" {
		out["employment_type"] = "full_time"
	}
	if n, ok := jarviNumber(jarviValue(raw, "joboffer_remote_days_per_week")).(float64); ok && n > 0 {
		out["job_location_type"] = "hybrid"
	}
	if detailTruthy(raw["publishedAt"]) {
		out["date_posted"] = raw["publishedAt"]
	}
	if cases.Fold().String(jarviValue(raw, "joboffer_salary_is_public")) == "true" {
		minimum, maximum := jarviNumber(jarviValue(raw, "joboffer_salary_per_year_min")), jarviNumber(jarviValue(raw, "joboffer_salary_per_year_max"))
		if minimum != nil || maximum != nil {
			if currency == "" {
				currency = "EUR"
			}
			out["base_salary"] = map[string]any{"min": minimum, "max": maximum, "currency": currency, "unit": "year"}
		}
	}
	metadata := map[string]any{}
	for target, source := range map[string]string{"source_id": "id", "short_id": "shortId", "updated_at": "updatedAt"} {
		if v := raw[source]; v != nil && v != "" {
			metadata[target] = v
		}
	}
	if n := jarviNumber(jarviValue(raw, "joboffer_min_years_of_experience")); n != nil {
		metadata["minimum_years_experience"] = n
	}
	if len(metadata) > 0 {
		out["metadata"] = metadata
	}
	return out, nil
}
