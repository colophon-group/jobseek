package apisniffer

import (
	"encoding/json"
	"math"
)

// BrowserDetailOptions describes the original implicit single-job capture.
// Unsupported launch or interaction controls remain outside admission.
type BrowserDetailOptions struct {
	Path                string
	Fields              map[string]any
	Wait                string
	TimeoutMS, SettleMS uint64
}

func BrowserDetailOptionsFromConfig(raw []byte) (BrowserDetailOptions, error) {
	o := BrowserDetailOptions{Wait: "load", TimeoutMS: 20000, SettleMS: 3000}
	d, err := Decode(raw)
	if err != nil {
		return o, ErrOptions
	}
	c, ok := d.Value.(map[string]any)
	if !ok {
		return o, ErrOptions
	}
	for key, value := range c {
		switch key {
		case "json_path":
			o.Path, ok = value.(string)
			if !ok || len(o.Path) > 8192 {
				return o, ErrOptions
			}
			if o.Path != "" {
				if _, err := Search(map[string]any{}, o.Path); err != nil {
					return o, ErrOptions
				}
			}
		case "fields":
			o.Fields, ok = value.(map[string]any)
			if !ok || len(o.Fields) > 128 {
				return o, ErrOptions
			}
			for key, field := range o.Fields {
				if key == "" || len(key) > 256 || ValidateField(field) != nil {
					return o, ErrOptions
				}
			}
		case "wait":
			o.Wait, ok = value.(string)
			if !ok || o.Wait != "commit" && o.Wait != "domcontentloaded" && o.Wait != "load" && o.Wait != "networkidle" {
				return o, ErrOptions
			}
		case "timeout":
			n, valid := integer(value)
			if !valid || n < 1 || n > 120000 {
				return o, ErrOptions
			}
			o.TimeoutMS = uint64(n)
		case "settle":
			n, valid := value.(json.Number)
			if !valid {
				return o, ErrOptions
			}
			seconds, e := n.Float64()
			if e != nil || math.IsNaN(seconds) || math.IsInf(seconds, 0) || seconds < 0 || seconds > 30 || seconds*1000 != math.Trunc(seconds*1000) {
				return o, ErrOptions
			}
			o.SettleMS = uint64(seconds * 1000)
		case "enrich":
			values, valid := value.([]any)
			if !valid {
				return o, ErrOptions
			}
			seen := map[string]bool{}
			for _, value := range values {
				key, valid := value.(string)
				if !valid || seen[key] {
					return o, ErrOptions
				}
				seen[key] = true
				switch key {
				case "title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary":
				default:
					return o, ErrOptions
				}
			}
		default:
			return o, ErrOptions
		}
	}
	return o, nil
}
