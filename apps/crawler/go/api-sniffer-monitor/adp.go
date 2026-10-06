package apisniffer

import (
	_ "embed"
	"encoding/json"
	"golang.org/x/text/cases"
	"html"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type ADPBoard struct{ CID, CCID, Locale string }

const adpListingPath = "/mascsr/default/mdf/recruitment/recruitment.html"
const adpSearchPath = "/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"

var adpID = regexp.MustCompile(`^[0-9]{1,32}_[0-9]{1,12}$`)
var adpLocale = regexp.MustCompile(`^[A-Za-z]{2}_[A-Za-z]{2}$`)

//go:embed adp_employment_types.json
var adpEmploymentTypesJSON []byte
var adpEmploymentTypes map[string]string

func init() {
	if json.Unmarshal(adpEmploymentTypesJSON, &adpEmploymentTypes) != nil {
		panic("invalid ADP employment rules")
	}
}

var adpSpaceComma = regexp.MustCompile(`\s+,`)

func fifthDirectMetadata(source, raw string) (map[string]any, error) {
	return fifthMetadata(source, raw, false)
}
func fifthMetadata(source, raw string, allowProxy bool) (map[string]any, error) {
	m, e := DecodeInlineMetadata(raw)
	if e != nil || !validURL(source) {
		return nil, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if key == "proxy" && allowProxy {
			if m[key] != nil && m[key] != true && m[key] != false {
				return nil, ErrOptions
			}
			continue
		}
		if detailTruthy(m[key]) {
			return nil, ErrOptions
		}
	}
	if m["ssl_verify"] != nil && m["ssl_verify"] != true {
		return nil, ErrOptions
	}
	return m, nil
}
func adpBoard(cid, cc, locale any) (ADPBoard, error) {
	c := bambooClean(cid)
	c = strings.ToLower(c)
	k := bambooClean(cc)
	l := bambooClean(locale)
	if !ukgUUID.MatchString(c) || !adpID.MatchString(k) || !adpLocale.MatchString(l) {
		return ADPBoard{}, ErrOptions
	}
	l = strings.ToLower(l[:2]) + "_" + strings.ToUpper(l[3:])
	return ADPBoard{c, k, l}, nil
}
func ADPBoardFromURL(source string) (ADPBoard, error) {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || len(source) > 4096 || strings.ToLower(u.Hostname()) != "workforcenow.adp.com" || u.Path != adpListingPath {
		return ADPBoard{}, ErrOptions
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil {
		return ADPBoard{}, ErrOptions
	}
	for k, v := range q {
		if len(v) != 1 || (k != "cid" && k != "ccId" && k != "lang" && k != "selectedMenuKey" && k != "jobId") {
			return ADPBoard{}, ErrOptions
		}
	}
	if v, ok := q["selectedMenuKey"]; ok && v[0] != "CareerCenter" {
		return ADPBoard{}, ErrOptions
	}
	if v, ok := q["jobId"]; ok && !adpID.MatchString(v[0]) {
		return ADPBoard{}, ErrOptions
	}
	return adpBoard(q.Get("cid"), q.Get("ccId"), q.Get("lang"))
}
func ADPOptionsFromMetadata(source, raw string) (ADPBoard, error) {
	m, e := fifthDirectMetadata(source, raw)
	if e != nil {
		return ADPBoard{}, e
	}
	cc := m["cc_id"]
	if !detailTruthy(cc) {
		cc = m["ccId"]
	}
	loc := m["locale"]
	if !detailTruthy(loc) {
		loc = m["lang"]
	}
	if b, e := adpBoard(m["cid"], cc, loc); e == nil {
		return b, nil
	}
	return ADPBoardFromURL(source)
}
func (b ADPBoard) query() string {
	return "cid=" + url.QueryEscape(b.CID) + "&ccId=" + url.QueryEscape(b.CCID) + "&lang=" + url.QueryEscape(b.Locale)
}
func (b ADPBoard) SearchURL(start int) string {
	return "https://workforcenow.adp.com" + adpSearchPath + "?" + b.query() + "&locale=" + url.QueryEscape(b.Locale) + "&%24skip=" + strconv.Itoa(start)
}
func (b ADPBoard) JobURL(id string) string {
	return "https://workforcenow.adp.com" + adpListingPath + "?" + b.query() + "&selectedMenuKey=CareerCenter&jobId=" + url.QueryEscape(id)
}
func (b ADPBoard) ResourceMatches(source string) bool {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || strings.ToLower(u.Hostname()) != "workforcenow.adp.com" || u.Path != adpSearchPath {
		return false
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil || len(q) != 5 {
		return false
	}
	for _, v := range q {
		if len(v) != 1 {
			return false
		}
	}
	n, e := strconv.Atoi(q.Get("$skip"))
	return e == nil && n >= 1 && n <= 50000 && q.Get("cid") == b.CID && q.Get("ccId") == b.CCID && q.Get("lang") == b.Locale && q.Get("locale") == b.Locale
}
func ADPPage(d *Document, start int) (int64, []any, error) {
	m, ok := d.Value.(map[string]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	rows, ok := m["jobRequisitions"].([]any)
	meta, mok := m["meta"].(map[string]any)
	if !ok || !mok {
		return 0, nil, ErrInventory
	}
	n, nok := meta["totalNumber"].(json.Number)
	s, sok := meta["startSequence"].(json.Number)
	if !nok || !sok {
		return 0, nil, ErrInventory
	}
	total, e := n.Int64()
	offset, oe := s.Int64()
	if e != nil || oe != nil || offset != int64(start) || total < 0 || len(rows) != int(min(int64(20), max(total-int64(start)+1, 0))) {
		return 0, nil, ErrInventory
	}
	for _, r := range rows {
		if _, ok := r.(map[string]any); !ok {
			return 0, nil, ErrInventory
		}
	}
	return total, rows, nil
}
func adpClean(v any) any {
	s := inlineTrim(html.UnescapeString(bambooClean(v)))
	if s == "" {
		return nil
	}
	return s
}
func ADPEmploymentType(raw any) string {
	s, ok := raw.(string)
	if !ok {
		return ""
	}
	label := strings.ToLower(s)
	label = strings.NewReplacer("-", " ", "_", " ", "/", " ").Replace(label)
	label = strings.Join(strings.Fields(label), " ")
	full, part := strings.Contains(label, "full time"), strings.Contains(label, "part time")
	switch {
	case full && part:
		return "full_or_part"
	case part:
		return "part_time"
	case full:
		return "full_time"
	case strings.Contains(label, "per diem") || strings.Contains(label, "seasonal"):
		return "part_time"
	case strings.Contains(label, "intern") || strings.Contains(label, "apprentice") || strings.Contains(label, "trainee"):
		return "internship"
	case strings.Contains(label, "temporary") || label == "temp":
		return "temporary"
	case strings.Contains(label, "contract") || strings.Contains(label, "consultant"):
		return "contract"
	}
	return adpEmploymentTypes[strings.ToLower(strings.TrimSpace(s))]
}
func ADPJobFields(raw any, b ADPBoard) map[string]any {
	row, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	id := bambooClean(row["itemID"])
	title := adpClean(row["requisitionTitle"])
	if !adpID.MatchString(id) || title == nil {
		return nil
	}
	locs := []string{}
	seen := map[string]bool{}
	if rows, ok := row["requisitionLocations"].([]any); ok {
		for _, r := range rows {
			m, ok := r.(map[string]any)
			if !ok {
				continue
			}
			nc, _ := m["nameCode"].(map[string]any)
			s, _ := adpClean(nc["shortName"]).(string)
			s = adpSpaceComma.ReplaceAllString(strings.Join(strings.Fields(s), " "), ",")
			key := cases.Fold().String(s)
			if s != "" && !seen[key] {
				seen[key] = true
				locs = append(locs, s)
			}
		}
	}
	var locations any
	if len(locs) > 0 {
		locations = locs
	}
	var employment any
	w, _ := row["workLevelCode"].(map[string]any)
	if value := ADPEmploymentType(w["shortName"]); value != "" {
		employment = value
	}
	md := map[string]any{"item_id": id}
	if value := adpClean(row["clientRequisitionID"]); value != nil {
		md["requisition_id"] = value
	}
	return map[string]any{"url": b.JobURL(id), "title": title, "locations": locations, "employment_type": employment, "date_posted": adpClean(row["postDate"]), "language": b.Locale[:2], "metadata": md, "description": nil, "extras": nil, "job_location_type": nil}
}
