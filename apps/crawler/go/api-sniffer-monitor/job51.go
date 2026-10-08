package apisniffer

import (
	"crypto/md5" // Public provider browser signing protocol, not a credential.
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

const job51API = "https://coapi.51job.com/"

// Public coapi.min.js signing slice at index1, length15, matching Python.
const job51SigningSlice = "uD&#mheJQBlgy&S"

var job51BoardPath = regexp.MustCompile(`^/(?:[A-Za-z0-9_-]{1,64}job_list|[A-Za-z0-9_-]{1,64}/job)\.html$`)
var job51Identity = regexp.MustCompile(`^[0-9]{1,20}$`)
var job51Date = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}`)
var job51Qualifications = regexp.MustCompile(`(?:岗位要求|任职要求|职位要求)\s*[:：]?`)
var job51EdgeBreaks = regexp.MustCompile(`(?i)^(?:<br\s*/?>)+|(?:<br\s*/?>)+$`)

func Job51BoardOrigin(board string) (string, error) {
	u, e := url.Parse(board)
	if e != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.RawQuery != "" || u.Fragment != "" || u.Port() != "" && u.Port() != "443" {
		return "", ErrOptions
	}
	host := strings.ToLower(u.Hostname())
	if !strings.HasSuffix(host, ".51job.com") || host == "www.51job.com" || host == "jobs.51job.com" || host == "coapi.51job.com" || !job51BoardPath.MatchString(u.Path) {
		return "", ErrOptions
	}
	return "https://" + host, nil
}

func job51SignedRequest(endpoint, parameters string) Request {
	sum := md5.Sum([]byte("coapi" + parameters + job51SigningSlice)) //nolint:gosec
	return Request{Method: "GET", URL: job51API + endpoint + "?key=1&sign=" + hex.EncodeToString(sum[:]) + "&params=" + url.QueryEscape(parameters)}
}
func Job51ListRequest(ctmid int64, page int) (Request, error) {
	if ctmid < 1 || ctmid > 999_999_999_999 || page < 1 || page > 2500 {
		return Request{}, ErrOptions
	}
	// Object order is part of the provider's signed protocol.
	return job51SignedRequest("job_list.php", fmt.Sprintf(`{"ctmid":%d,"pagesize":20,"pagenum":%d,"jobarea":"","issuedate":"","keyword":"","divid":"","poscode":""}`, ctmid, page)), nil
}
func Job51DetailRequest(id string) (Request, error) {
	if !job51Identity.MatchString(id) {
		return Request{}, ErrOptions
	}
	return job51SignedRequest("job_detail.php", `{"jobid":"`+id+`"}`), nil
}

func Job51JSONP(body []byte) (map[string]any, error) {
	if utf8.RuneCount(body) > 5_000_000 || !strings.HasPrefix(string(body), "jsoncallback(") || !strings.HasSuffix(string(body), ")") {
		return nil, ErrInventory
	}
	d, e := Decode(body[len("jsoncallback(") : len(body)-1])
	if e != nil {
		return nil, ErrInventory
	}
	root, ok := d.Value.(map[string]any)
	if !ok || root["status"] != "1" {
		return nil, ErrInventory
	}
	out, ok := root["resultbody"].(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	return out, nil
}

func job51Int(v any) (int64, error) {
	var raw string
	switch v := v.(type) {
	case json.Number:
		raw = string(v)
	case string:
		raw = v
	default:
		return 0, ErrInventory
	}
	if !smallUnsigned.MatchString(raw) {
		return 0, ErrInventory
	}
	n, e := strconv.ParseInt(raw, 10, 64)
	if e != nil || n < 0 {
		return 0, ErrInventory
	}
	return n, nil
}

func Job51ListPage(body map[string]any, page int) (int64, []map[string]any, error) {
	total, e := job51Int(body["totalnum"])
	if e != nil || page < 1 {
		return 0, nil, ErrInventory
	}
	rows, ok := body["joblist"].([]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	expected := total - int64(page-1)*20
	if expected < 0 {
		expected = 0
	}
	if expected > 20 {
		expected = 20
	}
	if int64(len(rows)) != expected {
		return 0, nil, ErrInventory
	}
	out := []map[string]any{}
	for _, v := range rows {
		row, ok := v.(map[string]any)
		if !ok {
			return 0, nil, ErrInventory
		}
		out = append(out, row)
	}
	return total, out, nil
}

func Job51JobFields(raw map[string]any, ctmid int64, expectedID string, normalize SmallDescriptionNormalizer) (map[string]any, error) {
	id, title := smallText(raw["jobid"]), smallText(raw["jobname"])
	returned, e := job51Int(raw["ctmid"])
	if e != nil || returned != ctmid || id != expectedID || !job51Identity.MatchString(id) || title == "" || normalize == nil {
		return nil, ErrInventory
	}
	rawDescription := smallText(raw["jobinfo"])
	if rawDescription == "" {
		return nil, ErrInventory
	}
	description, e := normalize(rawDescription)
	if e != nil || description == nil {
		return nil, ErrInventory
	}
	out := map[string]any{"url": "https://jobs.51job.com/all/" + id + ".html", "title": title, "description": *description, "language": "zh", "source_identity": fmt.Sprintf("job51:%d:%s", ctmid, id)}
	location := smallText(raw["jobareaname"])
	if location == "" {
		location = smallText(raw["workareaname"])
	}
	if location != "" {
		out["locations"] = []string{location}
	}
	if v := smallText(raw["term"]); v != "" {
		out["employment_type"] = v
	}
	if m := job51Date.FindString(smallText(raw["issuedate"])); m != "" {
		out["date_posted"] = m
	}
	extras := map[string]any{}
	skills := []string{}
	seen := map[string]bool{}
	for _, v := range strings.Fields(smallText(raw["jkeyword"])) {
		if !seen[v] {
			seen[v] = true
			skills = append(skills, v)
		}
	}
	if len(skills) > 0 {
		extras["skills"] = skills
	}
	responsibilities, qualifications := rawDescription, ""
	if m := job51Qualifications.FindStringIndex(rawDescription); m != nil {
		responsibilities = rawDescription[:m[0]]
		qualifications = rawDescription[m[1]:]
	}
	for key, part := range map[string]string{"responsibilities": responsibilities, "qualifications": qualifications} {
		v, e := normalize(part)
		if e != nil {
			return nil, e
		}
		if v != nil {
			if text := strings.TrimSpace(job51EdgeBreaks.ReplaceAllString(*v, "")); text != "" {
				extras[key] = text
			}
		}
	}
	if len(extras) > 0 {
		out["extras"] = extras
	}
	metadata := map[string]any{"id": id}
	for target, source := range map[string]string{"employer": "coname", "department": "divname", "address": "address", "job_function": "funtype", "experience": "workyearname", "education": "degreefrom", "salary_label": "providesalarname", "benefits": "jobwelf"} {
		if v := smallText(raw[source]); v != "" {
			metadata[target] = v
		}
	}
	out["metadata"] = metadata
	return out, nil
}
