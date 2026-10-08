package apisniffer

import (
	"encoding/json"
	stdhtml "html"
	"io"
	"net/url"
	"reflect"
	"regexp"
	"strconv"
	"strings"

	"golang.org/x/net/html"
	"golang.org/x/text/cases"
)

type UmantisRow struct {
	ID, Language, URL, Title, Location, EmploymentType string
}

type UmantisNavigation struct {
	Table, Next              string
	Total, First, Last, Page int
	NextActive               bool
}

var umantisVacancy = regexp.MustCompile(`^/Vacancies/([1-9][0-9]*)/Description/([1-9][0-9]*)/?$`)
var umantisTable = regexp.MustCompile(`^[1-9][0-9]{0,11}$`)
var umantisVoid = map[string]bool{"area": true, "base": true, "br": true, "col": true, "embed": true, "hr": true, "img": true, "input": true, "link": true, "meta": true, "param": true, "source": true, "track": true, "wbr": true}

func umantisIdentity(value string) string {
	return cases.Fold().String(strings.Join(strings.Fields(value), " "))
}

// Token boundaries preserve the existing parser's complete nested employer
// capture. Browser DOM repair must not turn a malformed owner field into proof.
func ParseUmantisRows(body string, o UmantisOptions) ([]UmantisRow, error) {
	rows := []UmantisRow{}
	var current UmantisRow
	inRow, inLink, invalid := false, false, false
	field, label, value, employer := "", "", "", ""
	var labels, values, employers []string
	owners := []string{}
	reset := func() {
		current = UmantisRow{}
		field = ""
		label = ""
		value = ""
		employer = ""
		labels = nil
		values = nil
		employers = nil
		owners = []string{}
		invalid = false
	}
	appendRow := func() error {
		current.Title = strings.TrimSpace(current.Title)
		if current.URL != "" && current.ID != "" && current.Language != "" && current.Title != "" {
			if o.Strict && (invalid || len(employers) > 0 || len(owners) != 1 || umantisIdentity(owners[0]) != umantisIdentity(o.Employer)) {
				return ErrInventory
			}
			rows = append(rows, current)
		}
		reset()
		return nil
	}
	end := func(tag string) error {
		for _, capture := range []struct {
			stack *[]string
			done  func()
		}{
			{&labels, func() {
				switch strings.TrimSuffix(umantisIdentity(label), ":") {
				case "anstellungsort", "arbeitsort", "standort", "ort", "location", "lieu", "localité", "localita", "località", "luogo", "sede":
					field = "location"
				case "art", "beschäftigungsart", "employment category", "employment type", "type d'emploi", "tipo di impiego":
					field = "employment_type"
				default:
					field = ""
				}
			}},
			{&values, func() {
				clean := strings.Join(strings.Fields(value), " ")
				if clean != "" {
					if field == "location" {
						current.Location = clean
					} else if field == "employment_type" {
						current.EmploymentType = clean
					}
				}
				value = ""
			}},
			{&employers, func() { owners = append(owners, employer); employer = "" }},
		} {
			stack := *capture.stack
			if len(stack) == 0 {
				continue
			}
			if stack[len(stack)-1] == tag {
				*capture.stack = stack[:len(stack)-1]
				if len(*capture.stack) == 0 {
					capture.done()
				}
			} else {
				for _, old := range stack {
					if old == tag {
						invalid = true
					}
				}
			}
		}
		if tag == "a" && inLink {
			inLink = false
			if !inRow {
				return appendRow()
			}
		}
		if tag == "li" && inRow {
			field = ""
			label = ""
		}
		if tag == "tr" && inRow {
			if err := appendRow(); err != nil {
				return err
			}
			inRow = false
		}
		return nil
	}
	tokens := html.NewTokenizer(strings.NewReader(body))
	for {
		kind := tokens.Next()
		t := tokens.Token()
		if kind == html.ErrorToken {
			if tokens.Err() != io.EOF {
				return nil, ErrInventory
			}
			break
		}
		if kind == html.TextToken {
			if inLink {
				current.Title += t.Data
			}
			if len(labels) > 0 {
				label += t.Data
			}
			if len(values) > 0 {
				value += t.Data
			}
			if len(employers) > 0 {
				employer += t.Data
			}
			continue
		}
		if kind == html.EndTagToken {
			if err := end(t.Data); err != nil {
				return nil, err
			}
			continue
		}
		if kind != html.StartTagToken && kind != html.SelfClosingTagToken {
			continue
		}
		a := map[string]string{}
		for _, attr := range t.Attr {
			a[attr.Key] = attr.Val
		}
		tag, cls := t.Data, a["class"]
		if !umantisVoid[tag] {
			if len(labels) > 0 {
				labels = append(labels, tag)
			}
			if len(values) > 0 {
				values = append(values, tag)
			}
			if len(employers) > 0 {
				employers = append(employers, tag)
			}
		}
		switch {
		case tag == "tr":
			if inRow {
				if err := appendRow(); err != nil {
					return nil, err
				}
			}
			inRow = true
			reset()
		case tag == "li" && inRow:
			field = ""
			label = ""
		case tag == "i" && inRow:
			if strings.Contains(cls, "icon-department") {
				field = "location"
			} else if strings.Contains(cls, "icon-jobtype") {
				field = "employment_type"
			}
		case tag == "span" && inRow:
			if strings.Contains(cls, "visually-hidden") && len(labels) == 0 {
				labels = []string{tag}
				label = ""
			} else if strings.Contains(cls, "column-value") && len(values) == 0 {
				values = []string{tag}
				value = ""
				if o.Strict && a["id"] == o.EmployerField {
					employers = []string{tag}
					employer = ""
				}
			}
		case tag == "a" && strings.Contains(cls, "HSTableLinkSubTitle") && strings.Contains(a["href"], "/Vacancies/"):
			resolved, err := PythonJoinURL(o.Origin+"/", stdhtml.UnescapeString(a["href"]))
			u, parseErr := url.Parse(resolved)
			if err != nil || parseErr != nil || u.User != nil || u.Scheme != "https" || !strings.EqualFold(u.Scheme+"://"+u.Host, o.Origin) {
				return nil, ErrInventory
			}
			match := umantisVacancy.FindStringSubmatch(u.Path)
			if match == nil {
				return nil, ErrInventory
			}
			inLink = true
			current.ID, current.Language = match[1], match[2]
			current.URL = o.Origin + "/Vacancies/" + match[1] + "/Description"
			current.Title = ""
		}
		if kind == html.SelfClosingTagToken {
			if err := end(tag); err != nil {
				return nil, err
			}
		}
	}
	return rows, nil
}

func DeduplicateUmantisRows(rows []UmantisRow) ([]UmantisRow, error) {
	out := []UmantisRow{}
	index := map[string]int{}
	for _, r := range rows {
		at, exists := index[r.ID]
		if !exists {
			index[r.ID] = len(out)
			out = append(out, r)
			continue
		}
		old := out[at]
		if old.Language == r.Language {
			if old != r {
				return nil, ErrInventory
			}
			continue
		}
		a, ok := decimalLess(r.Language, old.Language)
		if !ok {
			return nil, ErrInventory
		}
		if a {
			out[at] = r
		}
	}
	return out, nil
}

func decimalLess(a, b string) (bool, bool) {
	if !regexp.MustCompile(`^[1-9][0-9]*$`).MatchString(a) || !regexp.MustCompile(`^[1-9][0-9]*$`).MatchString(b) {
		return false, false
	}
	if len(a) != len(b) {
		return len(a) < len(b), true
	}
	return a < b, true
}

func ParseUmantisNavigation(body string) (*UmantisNavigation, error) {
	var result *UmantisNavigation
	tokens := html.NewTokenizer(strings.NewReader(body))
	for {
		kind := tokens.Next()
		if kind == html.ErrorToken {
			if tokens.Err() != io.EOF {
				return nil, ErrInventory
			}
			return result, nil
		}
		if kind != html.StartTagToken && kind != html.SelfClosingTagToken {
			continue
		}
		t := tokens.Token()
		if t.Data != "table-navigation" {
			continue
		}
		for _, a := range t.Attr {
			if a.Key != "initial-data-string" {
				continue
			}
			raw, err := DecodeInlineMetadata(stdhtml.UnescapeString(a.Val))
			if err != nil {
				return nil, ErrInventory
			}
			n := UmantisNavigation{}
			n.Table, _ = raw["TableNr"].(string)
			if !umantisTable.MatchString(n.Table) {
				return nil, ErrInventory
			}
			for key, target := range map[string]*int{"TableTotalLines": &n.Total, "TableFrom": &n.First, "TableTo": &n.Last, "TableCurrentPage": &n.Page} {
				v := raw[key]
				s, ok := v.(string)
				if !ok {
					number, isNumber := v.(json.Number)
					if !isNumber {
						return nil, ErrInventory
					}
					s = number.String()
				}
				if !regexp.MustCompile(`^[0-9]+$`).MatchString(s) {
					return nil, ErrInventory
				}
				*target, err = strconv.Atoi(s)
				if err != nil || *target < 0 || *target > 50000 {
					return nil, ErrInventory
				}
			}
			if n.Page < 1 || n.Page > 100 {
				return nil, ErrInventory
			}
			if v := raw["NextLink"]; v != nil {
				next, ok := v.(map[string]any)
				if !ok {
					return nil, ErrInventory
				}
				n.Next, _ = next["EnhancedUrl"].(string)
				switch v := next["FieldIsActive"].(type) {
				case string:
					n.NextActive = v == "1"
				case json.Number:
					number, err := strconv.ParseFloat(v.String(), 64)
					n.NextActive = err == nil && number == 1
				case bool:
					n.NextActive = v
				}
			}
			if result != nil && !reflect.DeepEqual(*result, n) {
				return nil, ErrInventory
			}
			result = &n
		}
	}
}

func (o UmantisOptions) NextURL(n UmantisNavigation, current string) (string, error) {
	if !n.NextActive || n.Next == "" {
		return "", ErrInventory
	}
	resolved, err := PythonJoinURL(current, stdhtml.UnescapeString(n.Next))
	u, parseErr := url.Parse(resolved)
	old, oldErr := url.Parse(current)
	if err != nil || parseErr != nil || oldErr != nil {
		return "", ErrInventory
	}
	u.Fragment = ""
	if !o.ResourceMatches(u.String()) || u.Path != old.Path {
		return "", ErrInventory
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(q["tc"+n.Table]) != 1 || q.Get("tc"+n.Table) != "p"+strconv.Itoa(n.Page+1) || len(q["_search_token"+n.Table]) != 1 || !regexp.MustCompile(`^[1-9][0-9]{0,63}$`).MatchString(q.Get("_search_token"+n.Table)) {
		return "", ErrInventory
	}
	u.Fragment = ""
	return u.String(), nil
}
