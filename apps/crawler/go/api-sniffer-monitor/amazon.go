package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const AmazonAPIURL = "https://www.amazon.jobs/en/search.json"
const AmazonCategoriesURL = "https://www.amazon.jobs/en/job_categories"
const AmazonProfile = "amazon.partitioned-items/v1"

type AmazonOptions struct{ Country, Category, BusinessCategory string }

func (o AmazonOptions) InitialURL() string {
	q := url.Values{"result_limit": {"100"}, "sort": {"recent"}, "offset": {"0"}}
	if o.Country != "" {
		q.Set("country", o.Country)
	}
	if o.Category != "" {
		q.Set("category[]", o.Category)
	}
	if o.BusinessCategory != "" {
		q.Set("business_category[]", o.BusinessCategory)
	}
	return AmazonAPIURL + "?" + q.Encode()
}

var amazonCountry = regexp.MustCompile(`^[A-Z]{3}$`)
var amazonCategory = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,127}$`)
var amazonCategoryLink = regexp.MustCompile(`/job-categories/([a-z0-9-]+)`)
var amazonDollarSalary = regexp.MustCompile(`\$([\d,]+(?:\.\d+)?)\s*/\s*(\w+).*?\$([\d,]+(?:\.\d+)?)\s*/\s*(\w+)`)
var amazonRangeSalary = regexp.MustCompile(`(?i)([\d,]+(?:\.\d+)?)\s*[-–—]\s*([\d,]+(?:\.\d+)?)\s+(\w{3})\s+(annually|hourly|monthly|per hour|per year|per month)`)

func AmazonOptionsFromMetadata(board, metadata string) (AmazonOptions, error) {
	o := AmazonOptions{}
	if board != "https://www.amazon.jobs/en/search" {
		return o, ErrOptions
	}
	d, err := Decode([]byte(metadata))
	if err != nil {
		return o, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return o, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "actions", "skip_ssl", "stealth"} {
		if detailTruthy(m[key]) {
			return o, ErrOptions
		}
	}
	for key, target := range map[string]*string{"country": &o.Country, "category": &o.Category, "business_category": &o.BusinessCategory} {
		if m[key] == nil {
			continue
		}
		value, ok := m[key].(string)
		if !ok {
			return o, ErrOptions
		}
		*target = value
	}
	if o.Country != "" && !amazonCountry.MatchString(o.Country) || o.Category != "" && !amazonCategory.MatchString(o.Category) || o.BusinessCategory != "" && !amazonCategory.MatchString(o.BusinessCategory) {
		return o, ErrOptions
	}
	return o, nil
}

func (o AmazonOptions) ResourceMatches(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Host != "www.amazon.jobs" || u.User != nil || u.Fragment != "" {
		return false
	}
	if u.Path == "/en/job_categories" || u.Path == "/content/job-categories" || u.Path == "/content/en/job-categories" {
		return u.RawQuery == "" && !u.ForceQuery
	}
	if u.Path != "/en/search.json" {
		return false
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return false
	}
	for k, v := range q {
		if len(v) != 1 {
			return false
		}
		switch k {
		case "result_limit":
			if v[0] != "100" {
				return false
			}
		case "sort":
			if v[0] != "recent" {
				return false
			}
		case "offset":
			n, e := strconv.Atoi(v[0])
			if e != nil || strconv.Itoa(n) != v[0] || n < 0 || n >= 10_000 || n%100 != 0 {
				return false
			}
		case "country":
			if !amazonCountry.MatchString(v[0]) || o.Country != "" && v[0] != o.Country {
				return false
			}
		case "category[]":
			if !amazonCategory.MatchString(v[0]) || o.Category != "" && v[0] != o.Category {
				return false
			}
		case "business_category[]":
			if v[0] != o.BusinessCategory || v[0] == "" {
				return false
			}
		default:
			return false
		}
	}
	return q.Get("result_limit") == "100" && q.Get("sort") == "recent" && len(q["offset"]) == 1 && (o.Country == "" || q.Get("country") == o.Country) && (o.Category == "" || q.Get("category[]") == o.Category) && (o.BusinessCategory == "" || q.Get("business_category[]") == o.BusinessCategory)
}

type amazonPage struct {
	rows  []map[string]any
	total int
	err   error
}

// Keep at most five decoded pages live. The callback commits only verified
// batches; a failed later page leaves its prefix and cannot delist unseen jobs.
func DiscoverAmazon(ctx context.Context, o AmazonOptions, fetch SmallProviderFetch, yield func([]map[string]any) error) (bool, error) {
	if ctx == nil || fetch == nil || yield == nil {
		return false, ErrOptions
	}
	base := url.Values{}
	if o.Country != "" {
		base.Set("country", o.Country)
	}
	if o.Category != "" {
		base.Set("category[]", o.Category)
	}
	if o.BusinessCategory != "" {
		base.Set("business_category[]", o.BusinessCategory)
	}
	clone := func(q url.Values) url.Values {
		c := url.Values{}
		for k, v := range q {
			c[k] = append([]string{}, v...)
		}
		return c
	}
	page := func(q url.Values, offset int) amazonPage {
		q = clone(q)
		q.Set("result_limit", "100")
		q.Set("sort", "recent")
		q.Set("offset", strconv.Itoa(offset))
		endpoint := AmazonAPIURL + "?" + q.Encode()
		if !o.ResourceMatches(endpoint) {
			return amazonPage{err: ErrOptions}
		}
		body, err := fetch(ctx, Request{Method: "GET", URL: endpoint})
		if err != nil {
			return amazonPage{err: err}
		}
		d, err := Decode(body)
		if err != nil {
			return amazonPage{err: ErrInventory}
		}
		m, ok := d.Value.(map[string]any)
		if !ok || detailTruthy(m["error"]) {
			return amazonPage{err: ErrInventory}
		}
		total := 0
		if m["hits"] != nil {
			n, ok := m["hits"].(json.Number)
			if !ok {
				return amazonPage{err: ErrInventory}
			}
			total, err = strconv.Atoi(string(n))
			if err != nil || total < 0 || total > 2_000_000 {
				return amazonPage{err: ErrInventory}
			}
		}
		rows := []map[string]any{}
		if m["jobs"] != nil {
			list, ok := m["jobs"].([]any)
			if !ok || len(list) > 100 {
				return amazonPage{err: ErrInventory}
			}
			for _, v := range list {
				row, ok := v.(map[string]any)
				if !ok {
					return amazonPage{err: ErrInventory}
				}
				rows = append(rows, row)
			}
		}
		if len(rows) == 0 && offset < total {
			return amazonPage{err: ErrInventory}
		}
		return amazonPage{rows: rows, total: total}
	}
	capped := false
	query := func(q url.Values, first *amazonPage, consume func([]map[string]any) error) (int, error) {
		p := amazonPage{}
		if first == nil {
			p = page(q, 0)
		} else {
			p = *first
		}
		if p.err != nil {
			return 0, p.err
		}
		total := p.total
		if err := consume(p.rows); err != nil {
			return total, err
		}
		p.rows = nil
		if capped {
			return total, nil
		}
		for start := 100; start < min(total, 10_000); start += 500 {
			if err := ctx.Err(); err != nil {
				return total, err
			}
			count := min(5, (min(total, 10_000)-start+99)/100)
			window := make([]amazonPage, count)
			var wg sync.WaitGroup
			for i := range window {
				wg.Add(1)
				go func(i int) { defer wg.Done(); window[i] = page(q, start+i*100) }(i)
			}
			wg.Wait()
			for i := range window {
				if window[i].err != nil {
					return total, window[i].err
				}
				if err := consume(window[i].rows); err != nil {
					return total, err
				}
				window[i].rows = nil
				if capped {
					return total, nil
				}
			}
		}
		return total, nil
	}
	seen := map[string]bool{}
	emit := func(rows []map[string]any) error {
		jobs := []map[string]any{}
		for _, r := range rows {
			job, err := AmazonJobFields(r)
			if err != nil {
				return err
			}
			if job == nil {
				continue
			}
			source := job["url"].(string)
			if seen[source] {
				continue
			}
			if len(seen) >= 50_000 {
				capped = true
				break
			}
			seen[source] = true
			jobs = append(jobs, job)
		}
		if len(jobs) > 0 {
			if err := yield(jobs); err != nil {
				return err
			}
		}
		if len(seen) >= 50_000 {
			capped = true
		}
		return nil
	}
	first := page(base, 0)
	if first.err != nil {
		return false, first.err
	}
	if first.total < 10_000 {
		_, err := query(base, &first, emit)
		return capped || err != nil, err
	}
	var categories []string
	getCategories := func() ([]string, error) {
		if categories != nil {
			return categories, nil
		}
		body, err := fetch(ctx, Request{Method: "GET", URL: AmazonCategoriesURL})
		// Transport/policy errors are not an authoritative fallback. The worker
		// seals reservation evidence even if a provider chooses fallback slugs.
		if err != nil {
			categories = append([]string{}, amazonFallbackCategories...)
			return categories, nil
		}
		seen := map[string]bool{}
		for _, m := range amazonCategoryLink.FindAllStringSubmatch(string(body), -1) {
			if !seen[m[1]] {
				seen[m[1]] = true
				categories = append(categories, m[1])
			}
		}
		if len(categories) > 128 {
			return nil, ErrInventory
		}
		if len(categories) == 0 {
			categories = append([]string{}, amazonFallbackCategories...)
		}
		return categories, nil
	}
	partial := false
	partitionCategories := func(q url.Values) error {
		if q.Get("category[]") != "" {
			partial = true
			return nil
		}
		slugs, err := getCategories()
		if err != nil {
			return err
		}
		for _, slug := range slugs {
			if capped {
				return nil
			}
			q := clone(q)
			q.Set("category[]", slug)
			total, err := query(q, nil, emit)
			if err != nil {
				return err
			}
			if total >= 10_000 {
				partial = true
			}
		}
		return nil
	}
	if o.Country != "" {
		_, err := query(base, &first, emit)
		if err != nil {
			return true, err
		}
		if !capped {
			err = partitionCategories(base)
		}
		return capped || partial, err
	}
	countrySet := map[string]bool{}
	scan := func(rows []map[string]any) error {
		for _, row := range rows {
			if raw := row["country_code"]; detailTruthy(raw) {
				country, ok := raw.(string)
				if !ok || !amazonCountry.MatchString(country) {
					return ErrInventory
				}
				countrySet[country] = true
			}
		}
		return nil
	}
	if _, err := query(base, &first, scan); err != nil {
		return true, err
	}
	countries := []string{}
	for country := range countrySet {
		countries = append(countries, country)
	}
	sort.Strings(countries)
	if len(countries) == 0 {
		countries = append(countries, amazonFallbackCountries...)
	}
	for _, country := range countries {
		if capped {
			break
		}
		q := clone(base)
		q.Set("country", country)
		total, err := query(q, nil, emit)
		if err != nil {
			return true, err
		}
		if total >= 10_000 && !capped {
			if err := partitionCategories(q); err != nil {
				return true, err
			}
		}
	}
	return capped || partial, nil
}

func AmazonJobFields(raw map[string]any) (map[string]any, error) {
	path, ok := raw["job_path"].(string)
	if !ok {
		if !detailTruthy(raw["job_path"]) {
			return nil, nil
		}
		return nil, ErrInventory
	}
	if path == "" {
		return nil, nil
	}
	u, err := url.Parse("https://www.amazon.jobs" + path)
	if err != nil || u.Scheme != "https" || u.Host != "www.amazon.jobs" || u.User != nil || u.Fragment != "" || !strings.HasPrefix(u.Path, "/en/jobs/") {
		return nil, ErrInventory
	}
	job := map[string]any{"url": u.String()}
	for key, field := range map[string]string{"title": "title", "employment_type": "job_schedule_type"} {
		if raw[field] != nil {
			job[key] = raw[field]
		}
	}
	parts := []string{}
	for _, field := range []string{"description", "basic_qualifications", "preferred_qualifications"} {
		value := raw[field]
		if !detailTruthy(value) {
			continue
		}
		text, ok := value.(string)
		if !ok {
			return nil, ErrInventory
		}
		if field == "basic_qualifications" {
			text = "<h3>Basic Qualifications</h3>\n" + text
		}
		if field == "preferred_qualifications" {
			text = "<h3>Preferred Qualifications</h3>\n" + text
		}
		parts = append(parts, text)
	}
	if len(parts) > 0 {
		job["description"] = strings.Join(parts, "\n")
	}
	for _, key := range []string{"normalized_location", "location"} {
		if detailTruthy(raw[key]) {
			text, ok := raw[key].(string)
			if !ok {
				return nil, ErrInventory
			}
			job["locations"] = []string{text}
			break
		}
	}
	metadata := map[string]any{}
	for _, key := range []string{"id_icims", "job_category", "job_family", "business_category", "company_name", "country_code"} {
		if detailTruthy(raw[key]) {
			metadata[key] = raw[key]
		}
	}
	if len(metadata) > 0 {
		job["metadata"] = metadata
	}
	if text, ok := raw["posted_date"].(string); ok {
		if d, e := time.Parse("January 2, 2006", strings.Join(strings.Fields(text), " ")); e == nil {
			job["date_posted"] = d.Format("2006-01-02")
		}
	}
	if text, ok := raw["salary"].(string); ok {
		if salary := amazonSalary(text); salary != nil {
			job["base_salary"] = salary
		}
	}
	return job, nil
}

func amazonSalary(text string) map[string]any {
	currency, low, high, unit := "USD", "", "", ""
	if m := amazonDollarSalary.FindStringSubmatch(text); m != nil {
		low, high, unit = m[1], m[3], strings.ToLower(m[2])
	} else if m := amazonRangeSalary.FindStringSubmatch(text); m != nil {
		low, high, currency, unit = m[1], m[2], strings.ToUpper(m[3]), strings.ToLower(m[4])
	} else {
		return nil
	}
	l, e1 := strconv.ParseFloat(strings.ReplaceAll(low, ",", ""), 64)
	h, e2 := strconv.ParseFloat(strings.ReplaceAll(high, ",", ""), 64)
	if e1 != nil || e2 != nil {
		return nil
	}
	for _, entry := range []struct {
		aliases   []string
		canonical string
	}{{[]string{"annually", "annual", "year", "yr", "yearly", "per year"}, "year"}, {[]string{"mo", "month", "monthly", "per month"}, "month"}, {[]string{"hour", "hr", "hourly", "per hour"}, "hour"}, {[]string{"week", "weekly"}, "week"}, {[]string{"day", "daily"}, "day"}} {
		for _, alias := range entry.aliases {
			if unit == alias {
				unit = entry.canonical
			}
		}
	}
	return map[string]any{"currency": currency, "min": l, "max": h, "unit": unit}
}

var amazonFallbackCountries = []string{"USA", "CAN", "CRI", "MEX", "COL", "BRA", "GBR", "DEU", "FRA", "IRL", "ESP", "ITA", "NLD", "LUX", "POL", "ROU", "CZE", "BEL", "FIN", "CHE", "SVK", "IND", "CHN", "JPN", "SGP", "AUS", "KOR", "ISR", "ARE", "SAU", "ZAF", "EGY", "MYS", "PHL", "TWN", "TUR", "VNM", "NZL", "HKG", "THA", "IDN", "JOR", "BGD", "PER", "CHL", "ARG"}

var amazonFallbackCategories = []string{"administrative-support", "audio-video-photography-production", "business-intelligence-data-engineering", "business-merchant-development", "buying-planning-instock-management", "customer-service", "data-science", "database-administration", "design", "economics", "editorial-writing-content-management", "facilities-maintenance-real-estate", "fgbs", "fulfillment-center-warehouse-associate", "fulfillment-operations-management", "hardware-development", "human-resources", "investigation-loss-prevention", "leadership-development-training", "legal", "machine-learning-science", "marketing", "medical-health-safety", "operations-it-support-engineering", "project-program-product-management-non-tech", "project-program-product-management-technical", "public-policy", "public-relations-communications", "research-science", "sales-advertising-account-management", "software-development", "solutions-architecture", "supply-chain-transportation-management", "systems-quality-security-engineering"}
