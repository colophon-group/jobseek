package apisniffer

import (
	_ "embed"
	"encoding/json"
	"math"
	"net/url"
	"strconv"
	"strings"
)

//go:embed provider_location_types.json
var providerLocationTypesJSON []byte
var providerLocationTypes map[string]string

func init() {
	if json.Unmarshal(providerLocationTypesJSON, &providerLocationTypes) != nil {
		panic("invalid original provider location types")
	}
}

func providerLocationType(v any) (string, error) {
	if v == nil {
		return "", nil
	}
	s, ok := v.(string)
	if !ok {
		return "", ErrInventory
	}
	key := strings.ToLower(strings.TrimSpace(s))
	out := providerLocationTypes[key]
	if out == "" && strings.HasSuffix(key, ")") {
		out = providerLocationTypes[strings.SplitN(key, " (", 2)[0]]
	}
	return out, nil
}

func providerLocations(row map[string]any, keys []string, unique bool) []string {
	parts := []string{}
	seen := map[string]bool{}
	for _, key := range keys {
		s := smallText(row[key])
		if s != "" && (!unique || !seen[s]) {
			parts = append(parts, s)
			seen[s] = true
		}
	}
	if len(parts) == 0 {
		return nil
	}
	return []string{strings.Join(parts, ", ")}
}

func providerNumber(v any, text bool) any {
	if s, ok := v.(string); ok {
		if !text || strings.TrimSpace(s) == "" {
			return nil
		}
		v = json.Number(strings.ReplaceAll(strings.TrimSpace(s), ",", ""))
	}
	n, ok := v.(json.Number)
	if !ok {
		return nil
	}
	f, e := strconv.ParseFloat(string(n), 64)
	if e != nil || math.IsInf(f, 0) || math.IsNaN(f) {
		return nil
	}
	return n
}

func providerCode(v any) int {
	if b, ok := v.(bool); ok {
		if b {
			return 1
		}
		return 0
	}
	n, e := smallInt(v, false)
	if e != nil {
		return -1
	}
	return n
}

func CuratelyJobFields(row map[string]any, tenant, currency, unit, language string) (map[string]any, error) {
	status := row["status"]
	code := -1
	switch status := status.(type) {
	case nil:
		code = 1
	case string:
		if len(status) == 1 && status[0] >= '0' && status[0] <= '5' {
			code = int(status[0] - '0')
		}
	default:
		code = providerCode(status)
	}
	if code >= 0 && code <= 5 && code != 1 {
		return nil, nil
	}
	if code != 1 {
		return nil, ErrInventory
	}
	id, e := smallInt(row["jobId"], true)
	title := smallText(row["jobTitle"])
	desc, _ := row["publicJobDescr"].(string)
	if e != nil || id <= 0 || title == "" || strings.TrimSpace(desc) == "" {
		return nil, ErrInventory
	}
	loc := providerLocations(row, []string{"workCity", "workState", "workZipcode"}, false)
	if len(loc) == 0 && providerCode(row["workType"]) == 1 {
		loc = []string{"Remote"}
	}
	if len(loc) == 0 {
		return nil, ErrInventory
	}
	md := map[string]any{"id": id}
	for _, p := range [][2]string{{"clientName", "client_name"}, {"estStartDate", "estimated_start_date"}, {"estEndDate", "estimated_end_date"}, {"jobHours", "job_hours_code"}, {"jobType", "job_type_code"}, {"workType", "work_type_code"}} {
		if row[p[0]] != nil {
			md[p[1]] = row[p[0]]
		}
	}
	fields := map[string]any{"url": "https://careers.curately.ai/jobs/" + tenant + "/apply-job/" + strconv.Itoa(id) + "/job", "title": title, "description": desc, "locations": loc, "metadata": md}
	hours, kind := providerCode(row["jobHours"]), providerCode(row["jobType"])
	employment := map[int]string{1: "full_time", 2: "part_time"}[hours]
	if kind == 1 {
		employment = "full_time"
		if hours == 2 {
			employment = "part_time"
		}
	} else if kind >= 2 && kind <= 4 {
		employment = "contract"
	}
	if employment != "" {
		fields["employment_type"] = employment
	}
	if location := map[int]string{1: "remote", 2: "hybrid", 3: "onsite"}[providerCode(row["workType"])]; location != "" {
		fields["job_location_type"] = location
	}
	if date, ok := row["createDate"].(string); ok {
		fields["date_posted"] = date
	}
	if language != "" {
		fields["language"] = language
	}
	min, max := providerNumber(row["payrateMin"], false), providerNumber(row["payrateMax"], false)
	if period := ADPSalaryUnit(unit); period != "" && currency != "" && (min != nil || max != nil) {
		fields["base_salary"] = map[string]any{"currency": strings.ToUpper(currency), "min": min, "max": max, "unit": period}
	}
	return fields, nil
}

func InploiJobFields(d *Document, row map[string]any, board, template string) (map[string]any, error) {
	id, title := row["id"], smallText(row["title"])
	if id == nil || id == "" || title == "" {
		return nil, nil
	}
	idText := tenthPythonString(d, id)
	base, e := url.Parse(board)
	if e != nil {
		return nil, ErrOptions
	}
	source := base.Scheme + "://" + base.Host + "/job/" + idText
	if template != "" {
		source = strings.ReplaceAll(template, "{id}", idText)
	}
	custom, _ := row["custom_data"].(map[string]any)
	metadata := map[string]any{}
	for key, v := range map[string]any{"id": id, "external_ref": row["external_ref"], "company_name": row["company_name"], "category": row["category"], "valid_through": custom["expiry_date"]} {
		if v != nil && v != "" {
			metadata[key] = v
		}
	}
	fields := map[string]any{"url": source, "title": title, "metadata": metadata}
	if loc := providerLocations(row, []string{"town", "city", "country"}, true); len(loc) > 0 {
		fields["locations"] = loc
	}
	for key, v := range map[string]any{"employment_type": firstTruthy(row["employment_type"], row["contract_type"]), "date_posted": firstTruthy(row["created_at"], custom["open_date"])} {
		if v != nil {
			fields[key] = v
		}
	}
	location := ""
	if strings.ToLower(tenthPythonString(d, row["location_type"])) != "location" {
		location, e = providerLocationType(row["location_type"])
		if e != nil {
			return nil, e
		}
	}
	if location == "" {
		text := strings.Join([]string{smallText(row["town"]), smallText(row["city"]), smallText(row["country"])}, " ")
		if strings.Contains(strings.ToLower(text), "remote") {
			location = "remote"
		}
	}
	if location != "" {
		fields["job_location_type"] = location
	}
	if row["pay_display"] != false && row["pay_mask"] != true {
		min, max := providerNumber(row["pay_min"], true), providerNumber(row["pay_max"], true)
		if min == nil && max == nil {
			min = providerNumber(row["pay"], true)
			max = min
		}
		if min != nil || max != nil {
			var unit any
			if s := ADPSalaryUnit(tenthPythonString(d, firstTruthy(row["pay_type"], ""))); s != "" {
				unit = s
			}
			fields["base_salary"] = map[string]any{"currency": firstTruthy(row["pay_currency"], row["currency_code"]), "min": min, "max": max, "unit": unit}
		}
	}
	return fields, nil
}

func firstTruthy(values ...any) any {
	for _, v := range values {
		if detailTruthy(v) {
			return v
		}
	}
	return nil
}

func JobConvoDetailFields(row map[string]any) map[string]any {
	fields := map[string]any{}
	for source, target := range map[string]string{"title": "title", "employment": "employment_type", "pub_date": "date_posted"} {
		if s, ok := row[source].(string); ok {
			fields[target] = s
		}
	}
	desc, _ := row["description"].(string)
	if strings.TrimSpace(desc) == "" {
		desc = ""
	}
	if benefits, ok := row["benefits"].(string); ok && strings.TrimSpace(benefits) != "" {
		if desc != "" {
			desc += "\n"
		}
		desc += "<h3>Benefits</h3>\n" + benefits
	}
	if desc != "" {
		fields["description"] = desc
	}
	if req, ok := row["requirements"].(string); ok && strings.TrimSpace(req) != "" {
		fields["extras"] = map[string]any{"qualifications": req}
	}
	if loc := providerLocations(row, []string{"city", "state", "country"}, true); len(loc) > 0 {
		fields["locations"] = loc
	}
	code := providerCode(row["type_work_location"])
	if s, ok := row["type_work_location"].(string); ok {
		if len(s) == 1 && s[0] >= '0' && s[0] <= '2' {
			code = int(s[0] - '0')
		}
	}
	if loc := map[int]string{0: "onsite", 1: "hybrid", 2: "remote"}[code]; loc != "" {
		fields["job_location_type"] = loc
	}
	if language, ok := row["company_language"].(string); ok {
		language = strings.ToLower(strings.SplitN(language, "-", 2)[0])
		if len(language) == 2 {
			fields["language"] = language
		}
	}
	if row["salary"] != nil && row["salary"] != "" {
		fields["base_salary"] = row["salary"]
	}
	md := map[string]any{}
	for _, key := range []string{"id", "company", "deadline", "level", "status"} {
		if v := row[key]; v != nil && v != "" {
			md[key] = v
		}
	}
	if len(md) > 0 {
		fields["metadata"] = md
	}
	return fields
}
