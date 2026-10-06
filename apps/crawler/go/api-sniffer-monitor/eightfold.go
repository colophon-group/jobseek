package apisniffer

import (
	"encoding/json"
	"math"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var eightfoldID = regexp.MustCompile(`/careers/job/([0-9]+)`)

func EightfoldJobID(source string) string {
	m := eightfoldID.FindStringSubmatch(source)
	if len(m) == 2 {
		return m[1]
	}
	return ""
}

// PCSX accepts a case-insensitive domain key; the position API retains the
// existing case-sensitive key and *.eightfold.ai fallback.
func EightfoldDomain(source string, pcsx bool) string {
	u, e := url.Parse(source)
	if e != nil {
		return ""
	}
	for _, pair := range strings.Split(u.RawQuery, "&") {
		key, value, found := strings.Cut(pair, "=")
		if !found {
			continue
		}
		key, e = url.QueryUnescape(key)
		if e != nil {
			continue
		}
		value, e = url.QueryUnescape(value)
		if e != nil || value == "" {
			continue
		}
		if key == "domain" || pcsx && strings.EqualFold(key, "domain") {
			return value
		}
	}
	host := strings.ToLower(u.Hostname())
	if !pcsx && strings.HasSuffix(host, ".eightfold.ai") {
		return strings.TrimSuffix(host, ".eightfold.ai")
	}
	return ""
}

func EightfoldInt(value any) (int64, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return 1, true
		}
		return 0, true
	case string:
		n, e := strconv.ParseInt(strings.TrimSpace(v), 10, 64)
		return n, e == nil
	case json.Number:
		if n, e := v.Int64(); e == nil {
			return n, true
		}
		n, e := v.Float64()
		if e != nil || math.IsNaN(n) || math.IsInf(n, 0) || n >= math.Exp2(63) || n < -math.Exp2(63) {
			return 0, false
		}
		return int64(n), true
	}
	return 0, false
}

func eightfoldDate(value any, detail bool) any {
	var seconds float64
	if detail {
		switch v := value.(type) {
		case bool:
			if v {
				seconds = 1
			}
		case json.Number:
			seconds, _ = v.Float64()
		default:
			return nil
		}
	} else {
		n, ok := EightfoldInt(value)
		if !ok {
			return nil
		}
		seconds = float64(n)
	}
	if seconds <= 0 || math.IsNaN(seconds) || math.IsInf(seconds, 0) || seconds >= 253402300800 {
		return nil
	}
	return time.Unix(int64(seconds), 0).UTC().Format("2006-01-02")
}

func EightfoldPCSXFields(raw map[string]any, source string) map[string]any {
	j := map[string]any{"url": source, "title": nil, "description": nil, "locations": nil, "employment_type": nil, "job_location_type": nil, "date_posted": eightfoldDate(raw["postedTs"], false), "base_salary": nil, "language": nil, "metadata": nil, "extras": nil, "localizations": nil, "source_identity": nil}
	if detailTruthy(raw["name"]) {
		j["title"] = raw["name"]
	}
	locations := raw["standardizedLocations"]
	if value, ok := locations.(string); ok {
		d, e := Decode([]byte(value))
		rows, list := []any(nil), false
		if e == nil {
			rows, list = d.Value.([]any)
		}
		if list {
			locations = rows
		} else {
			locations = []any{value}
		}
	}
	if values, ok := locations.([]any); ok && len(values) > 0 {
		j["locations"] = values
	}
	if value, ok := raw["workLocationOption"].(string); ok && value != "" {
		j["job_location_type"] = strings.ToLower(value)
	}
	meta := map[string]any{}
	for target, key := range map[string]string{"department": "department", "ats_job_id": "atsJobId"} {
		if detailTruthy(raw[key]) {
			meta[target] = raw[key]
		}
	}
	if len(meta) > 0 {
		j["metadata"] = meta
	}
	return j
}

func (d *Document) EightfoldDetailFields(raw map[string]any) (map[string]any, error) {
	values := map[string]any{"title": raw["posting_name"], "description": raw["job_description"], "locations": nil, "employment_type": nil, "job_location_type": nil, "date_posted": eightfoldDate(raw["t_create"], true), "base_salary": nil, "language": nil, "extras": nil, "metadata": nil}
	if !detailTruthy(values["title"]) {
		values["title"] = raw["name"]
	}
	var locations []string
	if rows, ok := raw["locations"].([]any); ok {
		for _, v := range rows {
			if detailTruthy(v) {
				text, e := d.String(v)
				if e != nil {
					return nil, e
				}
				locations = append(locations, strings.TrimSpace(text))
			}
		}
	}
	if len(locations) == 0 && detailTruthy(raw["location"]) {
		text, e := d.String(raw["location"])
		if e != nil {
			return nil, e
		}
		locations = []string{strings.TrimSpace(text)}
	}
	if len(locations) > 0 {
		values["locations"] = locations
	}
	meta := map[string]any{}
	for _, key := range []string{"ats_job_id", "display_job_id", "department", "business_unit"} {
		if detailTruthy(raw[key]) {
			meta[key] = raw[key]
		}
	}
	if len(meta) > 0 {
		values["metadata"] = meta
	}
	return values, nil
}

type EightfoldWatermark struct {
	MaxTS                         int64
	LastFullAt, LastIncrementalAt *time.Time
	IntervalDays                  int64
	intervalBoolean               bool
	Enabled                       *bool
	AutoFullCrawl                 bool
	Extra                         map[string]any
}

func EightfoldReadWatermark(metadata map[string]any) (EightfoldWatermark, error) {
	w := EightfoldWatermark{IntervalDays: 7, AutoFullCrawl: true, Extra: map[string]any{}}
	raw, ok := metadata["pcsx_watermark"].(map[string]any)
	if !ok {
		return w, nil
	}
	if detailTruthy(raw["max_ts"]) {
		var ok bool
		w.MaxTS, ok = EightfoldInt(raw["max_ts"])
		if !ok {
			return w, ErrOptions
		}
	}
	for key, target := range map[string]**time.Time{"last_full_at": &w.LastFullAt, "last_incremental_at": &w.LastIncrementalAt} {
		value, ok := raw[key].(string)
		if !ok || value == "" {
			continue
		}
		parsed, e := time.Parse(time.RFC3339Nano, value)
		if e == nil {
			*target = &parsed
		}
	}
	if value, ok := raw["interval_days"].(json.Number); ok {
		if n, e := value.Int64(); e == nil && n > 0 {
			w.IntervalDays = n
		}
	} else if value, ok := raw["interval_days"].(bool); ok && value {
		w.IntervalDays = 1
		w.intervalBoolean = true
	}
	if value, ok := raw["enabled"].(bool); ok {
		w.Enabled = &value
	}
	if value, ok := raw["auto_full_crawl"].(bool); ok {
		w.AutoFullCrawl = value
	}
	if value, ok := raw["extra"].(map[string]any); ok {
		w.Extra = value
	}
	return w, nil
}
func (w EightfoldWatermark) NeedsFull(now time.Time) bool {
	if w.MaxTS == 0 || w.LastFullAt == nil {
		return true
	}
	// Avoid duration overflow for a large configured interval.
	return now.Sub(*w.LastFullAt).Hours()/24 >= float64(w.IntervalDays)
}
func (w EightfoldWatermark) Patch() map[string]any {
	inner := map[string]any{"max_ts": w.MaxTS, "interval_days": w.IntervalDays, "auto_full_crawl": w.AutoFullCrawl}
	if w.intervalBoolean {
		inner["interval_days"] = true
	}
	for key, value := range map[string]*time.Time{"last_full_at": w.LastFullAt, "last_incremental_at": w.LastIncrementalAt} {
		if value != nil {
			format := "2006-01-02T15:04:05-07:00"
			if value.Nanosecond() != 0 {
				format = "2006-01-02T15:04:05.000000-07:00"
			}
			inner[key] = value.Format(format)
		}
	}
	if w.Enabled != nil {
		inner["enabled"] = *w.Enabled
	}
	if len(w.Extra) > 0 {
		inner["extra"] = w.Extra
	}
	return map[string]any{"pcsx_watermark": inner}
}

type EightfoldDetailRoute struct{ SourceURL, APIURL string }

func EightfoldDetailRouteForSource(source string) (EightfoldDetailRoute, error) {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || u.RawPath != "" || u.Fragment != "" {
		return EightfoldDetailRoute{}, ErrOptions
	}
	id, domain := EightfoldJobID(u.Path), EightfoldDomain(source, false)
	if id == "" || len(id) > 64 || domain == "" || len(domain) > 256 {
		return EightfoldDetailRoute{}, ErrOptions
	}
	return EightfoldDetailRoute{source, "https://" + u.Hostname() + "/api/apply/v2/jobs/" + id + "?domain=" + url.QueryEscape(domain)}, nil
}
func (r EightfoldDetailRoute) ResourceMatches(source string) bool {
	return source == r.SourceURL || source == r.APIURL
}
