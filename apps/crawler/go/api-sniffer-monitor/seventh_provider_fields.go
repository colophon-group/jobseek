package apisniffer

import (
	"encoding/json"
	"strconv"
	"strings"
)

func seventhObject(v any) map[string]any { m, _ := v.(map[string]any); return m }
func seventhRows(v any) []any            { r, _ := v.([]any); return r }
func seventhTrim(v any) string           { s, _ := v.(string); return strings.TrimSpace(s) }
func seventhJoinedNames(v any, child string) []string {
	out := []string{}
	for _, row := range seventhRows(v) {
		m := seventhObject(seventhObject(row)[child])
		if s, ok := m["name"].(string); ok && s != "" {
			out = append(out, s)
		}
	}
	return out
}

func (d *Document) SeventhProviderRows(provider string) ([]any, error) {
	if rows, ok := d.Value.([]any); ok && provider != "hibob" {
		return rows, nil
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	key := "jobPostings"
	if provider == "hibob" {
		key = "jobAdDetails"
	}
	rows, ok := m[key].([]any)
	if !ok {
		return nil, ErrInventory
	}
	return rows, nil
}

func (d *Document) SeventhProviderJobFields(raw any, o SeventhProviderOptions) (map[string]any, error) {
	m, ok := raw.(map[string]any)
	if !ok {
		return nil, nil
	}
	out := map[string]any{}
	metadata := map[string]any{}
	switch o.Provider {
	case "deel":
		if !detailTruthy(m["id"]) {
			return nil, nil
		}
		id, e := d.String(m["id"])
		if e != nil {
			return nil, e
		}
		out["url"] = "https://jobs.deel.com/" + o.Slug + "/job-details/" + id + "/overview"
		out["title"], out["description"], out["date_posted"] = m["title"], m["richtextDescription"], m["createdAt"]
		job := seventhObject(m["job"])
		if loc := seventhJoinedNames(job["jobLocations"], "location"); len(loc) > 0 {
			out["locations"] = loc
		}
		if emp := seventhJoinedNames(job["jobEmploymentTypes"], "employmentType"); len(emp) > 0 {
			out["employment_type"] = emp[0]
		}
		if names := seventhJoinedNames(job["jobTeams"], "team"); len(names) > 0 {
			metadata["team"] = strings.Join(names, ", ")
		}
		if names := seventhJoinedNames(job["jobDepartments"], "department"); len(names) > 0 {
			metadata["department"] = strings.Join(names, ", ")
		}
		metadata["id"] = m["id"]
		comp := seventhObject(job["currentCompensation"])
		if detailTruthy(m["isCompensationVisible"]) && (comp["minAmount"] != nil || comp["maxAmount"] != nil) {
			out["base_salary"] = map[string]any{"currency": comp["currencyIsoCode"], "min": comp["minAmount"], "max": comp["maxAmount"], "unit": "year"}
		}
	case "hibob":
		id := seventhTrim(m["id"])
		if id == "" {
			return nil, nil
		}
		out["url"], out["title"], out["date_posted"], out["language"] = o.Origin+"/jobs/"+id, m["title"], m["publishedAt"], m["language"]
		parts := []string{}
		extras := map[string]any{}
		if s := seventhTrim(m["description"]); s != "" {
			parts = append(parts, s)
		}
		for _, v := range [][3]string{{"responsibilities", "Responsibilities", "responsibilities"}, {"requirements", "Requirements", "qualifications"}, {"benefits", "Benefits", "benefits"}} {
			if s := seventhTrim(m[v[0]]); s != "" {
				parts = append(parts, "<h3>"+v[1]+"</h3>\n"+s)
				extras[v[2]] = s
			}
		}
		if len(parts) > 0 {
			out["description"] = strings.Join(parts, "\n")
		}
		if len(extras) > 0 {
			out["extras"] = extras
		}
		loc := seventhTrim(m["site"])
		if loc == "" {
			loc = seventhTrim(m["country"])
		}
		if loc != "" {
			out["locations"] = []string{loc}
		}
		out["employment_type"] = m["employmentTypeId"]
		if !detailTruthy(out["employment_type"]) {
			out["employment_type"] = m["employmentType"]
		}
		out["job_location_type"] = m["workspaceType"]
		if !detailTruthy(out["job_location_type"]) {
			out["job_location_type"] = m["workspaceTypeId"]
		}
		for from, to := range map[string]string{"id": "id", "department": "department", "departmentId": "department_id", "siteId": "site_id", "country": "country", "employmentTypeId": "employment_type_id", "workspaceTypeId": "workspace_type_id"} {
			if v := m[from]; v != nil && v != "" {
				metadata[to] = v
			}
		}
		if m["payTransparencyMinSalary"] != nil || m["payTransparencyMaxSalary"] != nil {
			unit := ADPSalaryUnit(m["payTransparencySalaryPayPeriod"])
			if unit == "" {
				unit = "year"
			}
			out["base_salary"] = map[string]any{"currency": m["payTransparencySalaryCurrency"], "min": m["payTransparencyMinSalary"], "max": m["payTransparencyMaxSalary"], "unit": unit}
		}
	case "traffit":
		if detailTruthy(m["awarded"]) || !detailTruthy(m["url"]) {
			return nil, nil
		}
		advert, options := seventhObject(m["advert"]), seventhObject(m["options"])
		value := func(id string) any {
			for _, r := range seventhRows(advert["values"]) {
				v := seventhObject(r)
				if v["field_id"] == id {
					return v["value"]
				}
			}
			return nil
		}
		out["url"], out["title"], out["description"], out["language"] = m["url"], advert["name"], value("description"), advert["language"]
		if s, ok := m["valid_start"].(string); ok && s != "" {
			out["date_posted"] = strings.SplitN(s, " ", 2)[0]
		}
		if types := seventhRows(options["job_type"]); len(types) > 0 && detailTruthy(types[0]) {
			out["employment_type"] = types[0]
		}
		if options["remote"] == "1" || options["_work_model"] == "Remote" {
			out["job_location_type"] = "remote"
		} else if options["_work_model"] == "Hybrid" {
			out["job_location_type"] = "hybrid"
		}
		if s, ok := value("geolocation").(string); ok {
			geoDoc, e := Decode([]byte(s))
			if e == nil {
				geo := seventhObject(geoDoc.Value)
				if detailTruthy(geo["locality"]) {
					loc, e := d.String(geo["locality"])
					if e != nil {
						return nil, e
					}
					if detailTruthy(geo["country"]) {
						country, e := d.String(geo["country"])
						if e != nil {
							return nil, e
						}
						loc += ", " + country
					}
					out["locations"] = []string{loc}
				}
			}
		}
		extras := map[string]any{}
		for _, key := range []string{"requirements", "responsibilities", "benefits"} {
			if v := value(key); detailTruthy(v) {
				extras[key] = v
			}
		}
		if len(extras) > 0 {
			out["extras"] = extras
		}
		if v := seventhObject(advert["recruitment"])["nr_ref"]; detailTruthy(v) {
			metadata["reference"] = v
		}
		if v := options["branches"]; detailTruthy(v) {
			metadata["department"] = v
		}
		if detailTruthy(options["_Salary_Currency"]) && (options["_Salary_MIN"] != nil || options["_Salary_MAX"] != nil) {
			num := func(v any) any {
				if v == nil {
					return nil
				}
				if v == true {
					return float64(1)
				}
				if v == false {
					return float64(0)
				}
				var s string
				switch n := v.(type) {
				case json.Number:
					s = n.String()
				case string:
					s = n
				default:
					return nil
				}
				n, e := strconv.ParseFloat(strings.TrimSpace(s), 64)
				if e != nil {
					return nil
				}
				return n
			}
			unit := ADPSalaryUnit(options["_Salary_Rate"])
			if unit == "" {
				unit = "month"
			}
			out["base_salary"] = map[string]any{"currency": options["_Salary_Currency"], "min": num(options["_Salary_MIN"]), "max": num(options["_Salary_MAX"]), "unit": unit}
		}
	default:
		return nil, ErrOptions
	}
	source, ok := out["url"].(string)
	if !ok || !validURL(source) {
		return nil, ErrInventory
	}
	if len(metadata) > 0 {
		out["metadata"] = metadata
	}
	return out, nil
}
