package worker

import (
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"math"
	"strconv"
	"strings"
)

// Mirror the observed public c/jobDetail component's campaign/location gates,
// salary interpolation and disclosure order. Inputs remain untrusted data.
func composeAuraDescription(job map[string]any, locations []map[string]any, description, discloseLocations string) (string, error) {
	fail := errors.New("invalid public Aura description data")
	if description == "" || strings.TrimSpace(discloseLocations) == "" || len(description) > 4_000_000 || len(locations) > 100 || len(discloseLocations) > 4096 {
		return "", fail
	}
	campus := false
	if v, exists := job["Is_Campus_Campaign_Event__c"]; exists && v != nil {
		var ok bool
		campus, ok = v.(bool)
		if !ok {
			return "", fail
		}
	}
	base, ny, pl := false, false, false
	labels := strings.Split(discloseLocations, ",")
	if len(labels) > 32 {
		return "", fail
	}
	for _, location := range locations {
		v := location["Name"]
		if v == nil {
			continue
		}
		name, ok := v.(string)
		if !ok || len(name) > 1024 {
			return "", fail
		}
		for _, label := range labels {
			if label == "" {
				return "", fail
			}
			if strings.Contains(name, label) {
				base = true
			}
		}
		ny = ny || strings.Contains(name, "New York") || strings.Contains(name, "San Francisco") || strings.Contains(name, "Chicago")
		pl = pl || strings.Contains(name, "Poland")
	}
	document := "<!doctype html><html><body><h2>Job details</h2><p>Context</p><p>Context</p>" + description
	if !campus && base {
		value := func(key string) (string, error) {
			v := job[key]
			switch x := v.(type) {
			case nil:
				return "", nil
			case string:
				if len(x) > 128 {
					return "", fail
				}
				return x, nil
			case float64:
				if math.IsNaN(x) || math.IsInf(x, 0) || math.Abs(x) > 1e12 {
					return "", fail
				}
				return strconv.FormatFloat(x, 'f', -1, 64), nil
			case json.Number:
				f, e := x.Float64()
				if e != nil || math.IsNaN(f) || math.IsInf(f, 0) || math.Abs(f) > 1e12 {
					return "", fail
				}
				return strconv.FormatFloat(f, 'f', -1, 64), nil
			}
			return "", fail
		}
		currency, e := value("Currency__c")
		if e != nil {
			return "", e
		}
		low, e := value("Base_Compensation_Low__c")
		if e != nil {
			return "", e
		}
		high, e := value("Base_Compensation_High__c")
		if e != nil {
			return "", e
		}
		frequency := ""
		if v := job["Frequency__c"]; v != nil {
			var ok bool
			frequency, ok = v.(string)
			if !ok || len(frequency) > 128 {
				return "", fail
			}
			frequency = strings.ToLower(frequency)
		}
		for _, rule := range []struct {
			enabled bool
			prefix  string
		}{{ny, "With respect to NY, CA, and IL based applicants"}, {pl, "With respect to PL-based applicants"}} {
			if !rule.enabled {
				continue
			}
			paragraph := fmt.Sprintf("%s, the starting base pay range for this role is between %s %s and %s %s %s. The actual base pay is dependent upon several factors, including, but not limited to, relevant experience, business needs and market demands. This role may also be eligible for bonus compensation and employee benefits.", rule.prefix, currency, low, currency, high, frequency)
			document += "<p>" + html.EscapeString(paragraph) + "</p>"
		}
	}
	return document + "</body></html>", nil
}
