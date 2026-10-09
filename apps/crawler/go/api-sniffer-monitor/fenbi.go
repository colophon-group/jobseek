package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	xhtml "golang.org/x/net/html"
)

var fenbiDateExpression = regexp.MustCompile(`^new Date\([0-9]{4},[0-9]{1,2},[0-9]{1,2}\)\.getTime\(\)`)
var fenbiKey = regexp.MustCompile(`^[A-Za-z_$][A-Za-z0-9_$]*`)
var fenbiDate = regexp.MustCompile(`^([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日$`)
var fenbiPreferredLocation = regexp.MustCompile(`[（(]优先[）)]$`)
var fenbiBundlePath = regexp.MustCompile(`^/weblts_spa_online/page/main-[A-Z0-9]+\.js$`)

func FenbiMainBundleURL(page, board string) (string, error) {
	base, err := url.Parse(board)
	if err != nil || len(page) > 2_000_000 {
		return "", ErrInventory
	}
	root, err := xhtml.Parse(strings.NewReader(page))
	if err != nil {
		return "", ErrInventory
	}
	matches := []string{}
	var walk func(*xhtml.Node)
	walk = func(node *xhtml.Node) {
		if node.Type == xhtml.ElementNode && node.Data == "script" {
			for _, attribute := range node.Attr {
				if attribute.Key != "src" {
					continue
				}
				reference, err := url.Parse(attribute.Val)
				if err == nil {
					candidate := base.ResolveReference(reference)
					if candidate.Scheme == "https" && candidate.Hostname() == "nodestatic.fbstatic.cn" && candidate.User == nil && candidate.RawQuery == "" && !candidate.ForceQuery && candidate.Fragment == "" && fenbiBundlePath.MatchString(candidate.Path) {
						matches = append(matches, candidate.String())
					}
				}
				break
			}
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(root)
	if len(matches) != 1 {
		return "", ErrInventory
	}
	return matches[0], nil
}

func DiscoverFenbi(ctx context.Context, board, kind string, fetch SmallProviderFetch) ([]map[string]any, bool, error) {
	if ctx == nil || fetch == nil || kind != "fulltime" && kind != "parttime" {
		return nil, false, ErrOptions
	}
	path := "/page/joinus"
	if kind == "parttime" {
		path += "/parttime"
	}
	u, err := url.Parse(board)
	if err != nil || u.Scheme != "https" || u.Hostname() != "www.fenbi.com" || u.User != nil || u.Path != path || u.RawQuery != "" || u.Fragment != "" {
		return nil, false, ErrOptions
	}
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	page, err := fetch(ctx, Request{Method: "GET", URL: board})
	if err != nil {
		return nil, false, err
	}
	bundleURL, err := FenbiMainBundleURL(string(page), board)
	if err != nil {
		return nil, false, err
	}
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	bundle, err := fetch(ctx, Request{Method: "GET", URL: bundleURL})
	if err != nil {
		return nil, false, err
	}
	root, err := FenbiInventoryLiteral(string(bundle))
	if err != nil {
		return nil, false, err
	}
	jobs, err := FenbiJobFields(root, kind, board)
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	return jobs, false, err
}

// Convert only the original provider's bounded object-literal grammar; no
// JavaScript is evaluated and expressions inside quoted strings are retained.
func FenbiInventoryLiteral(bundle string) (map[string]any, error) {
	const marker = "this.joinUsArr="
	if len(bundle) > 5_000_000 || strings.Count(bundle, marker) != 1 || !utf8.ValidString(bundle) {
		return nil, ErrInventory
	}
	start := strings.Index(bundle, marker) + len(marker)
	if start >= len(bundle) || bundle[start] != '{' {
		return nil, ErrInventory
	}
	depth, end := 0, -1
	quoted, escaped := false, false
	for i := start; i < len(bundle); i++ {
		c := bundle[i]
		if quoted {
			if escaped {
				escaped = false
			} else if c == '\\' {
				escaped = true
			} else if c == '"' {
				quoted = false
			}
			continue
		}
		switch c {
		case '"':
			quoted = true
		case '\'', '`':
			return nil, ErrInventory
		case '{':
			depth++
		case '}':
			depth--
			if depth == 0 {
				end = i + 1
			}
		}
		if end >= 0 {
			break
		}
	}
	if end < 0 || utf8.RuneCountInString(bundle[start:end]) > 500000 {
		return nil, ErrInventory
	}
	literal := bundle[start:end]
	var out strings.Builder
	quoted, escaped = false, false
	for i := 0; i < len(literal); {
		c := literal[i]
		if quoted {
			out.WriteByte(c)
			if escaped {
				escaped = false
			} else if c == '\\' {
				escaped = true
			} else if c == '"' {
				quoted = false
			}
			i++
			continue
		}
		if c == '"' {
			quoted = true
			out.WriteByte(c)
			i++
			continue
		}
		if c == '\'' || c == '`' {
			return nil, ErrInventory
		}
		if strings.HasPrefix(literal[i:], "new Date(") {
			if match := fenbiDateExpression.FindString(literal[i:]); match != "" {
				out.WriteByte('0')
				i += len(match)
				continue
			}
		}
		out.WriteByte(c)
		i++
		if c != '{' && c != ',' {
			continue
		}
		space := i
		for i < len(literal) {
			r, size := utf8.DecodeRuneInString(literal[i:])
			if !unicode.IsSpace(r) && !(r >= 0x1c && r <= 0x1f) {
				break
			}
			i += size
		}
		out.WriteString(literal[space:i])
		key := fenbiKey.FindString(literal[i:])
		if key == "" {
			continue
		}
		colon := i + len(key)
		for colon < len(literal) {
			r, size := utf8.DecodeRuneInString(literal[colon:])
			if !unicode.IsSpace(r) && !(r >= 0x1c && r <= 0x1f) {
				break
			}
			colon += size
		}
		if colon < len(literal) && literal[colon] == ':' {
			out.WriteByte('"')
			out.WriteString(key)
			out.WriteByte('"')
			out.WriteString(literal[i+len(key) : colon+1])
			i = colon + 1
		}
	}
	d, err := Decode([]byte(out.String()))
	if err != nil {
		return nil, ErrInventory
	}
	root, ok := d.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	return root, nil
}

func FenbiJobFields(payload map[string]any, kind, board string) ([]map[string]any, error) {
	rows, ok := payload[kind].([]any)
	if !ok || len(rows) == 0 || len(rows) > 500 || kind != "fulltime" && kind != "parttime" {
		return nil, ErrInventory
	}
	jobs := []map[string]any{}
	seen := map[string]bool{}
	for _, value := range rows {
		row, ok := value.(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		id, ok := row["id"].(json.Number)
		if !ok || !smallUnsigned.MatchString(string(id)) || strings.TrimLeft(string(id), "0") == "" || seen[string(id)] {
			return nil, ErrInventory
		}
		seen[string(id)] = true
		title, location := smallText(row["title"]), smallText(row["location"])
		if title == "" || location == "" {
			return nil, ErrInventory
		}
		list := func(key string, empty bool) ([]string, error) {
			values, ok := row[key].([]any)
			if !ok || len(values) == 0 && !empty {
				return nil, ErrInventory
			}
			out := []string{}
			for _, value := range values {
				text := smallText(value)
				if text == "" {
					return nil, ErrInventory
				}
				out = append(out, text)
			}
			return out, nil
		}
		responsibilities, err := list("description", false)
		if err != nil {
			return nil, err
		}
		qualifications, err := list("requirements", true)
		if err != nil {
			return nil, err
		}
		section := func(title string, items []string) string {
			out := "<h2>" + title + "</h2><ol>"
			for _, item := range items {
				out += "<li>" + eighthEscape(item) + "</li>"
			}
			return out + "</ol>"
		}
		description := section("岗位职责", responsibilities)
		if len(qualifications) > 0 {
			description += section("任职要求", qualifications)
		}
		locations, workType := []string{}, ""
		if location == "网络办公" {
			locations, workType = []string{"China"}, "remote"
		} else {
			for _, part := range strings.Split(location, "、") {
				part = strings.TrimSpace(fenbiPreferredLocation.ReplaceAllString(strings.TrimSpace(part), ""))
				if part == "居家" {
					workType = "hybrid"
				} else if part == "全国" {
					locations = append(locations, "China")
				} else if part != "" {
					locations = append(locations, part)
				}
			}
		}
		if len(locations) == 0 {
			return nil, ErrInventory
		}
		employment := "full_time"
		if kind == "parttime" {
			switch {
			case strings.Contains(title, "实习"):
				employment = "internship"
			case strings.Contains(title, "兼职"):
				employment = "part_time"
			default:
				return nil, ErrInventory
			}
		}
		date := fenbiDate.FindStringSubmatch(smallText(row["publicDateShow"]))
		if date == nil {
			return nil, ErrInventory
		}
		year, _ := strconv.Atoi(date[1])
		month, _ := strconv.Atoi(date[2])
		day, _ := strconv.Atoi(date[3])
		posted := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
		if year < 1 || posted.Year() != year || int(posted.Month()) != month || posted.Day() != day {
			return nil, ErrInventory
		}
		identity := "fenbi:careers:" + kind + "-" + string(id)
		if !explicitNextdataIdentity.MatchString(identity) {
			return nil, ErrInventory
		}
		u, err := url.Parse(board)
		if err != nil {
			return nil, ErrInventory
		}
		department := row["department"]
		if !detailTruthy(department) {
			department = nil
		}
		fields := map[string]any{
			"url":   u.ResolveReference(&url.URL{Path: "/page/joinusdetail/" + kind + "/" + string(id)}).String(),
			"title": title, "description": description, "locations": locations, "employment_type": employment,
			"date_posted": posted.Format("2006-01-02"), "source_identity": identity,
			"extras":   map[string]any{"responsibilities": responsibilities, "qualifications": qualifications},
			"metadata": map[string]any{"provider_id": id, "department": department},
		}
		if workType != "" {
			fields["job_location_type"] = workType
		}
		jobs = append(jobs, fields)
	}
	return jobs, nil
}
