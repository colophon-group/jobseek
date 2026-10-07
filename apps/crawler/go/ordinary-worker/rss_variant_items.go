package worker

import (
	"bytes"
	"encoding/xml"
	"errors"
	"html"
	"io"
	"net/url"
	"regexp"
	"strings"
	"unicode"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

var sfXMLCompany = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func sfLegacyXMLIdentity(source string) (string, string, error) {
	u, err := url.Parse(source)
	if err != nil || !strings.EqualFold(u.Scheme, "https") || u.Hostname() == "" || u.User != nil || u.Fragment != "" || u.Port() != "" && u.Port() != "443" || !strings.EqualFold(strings.TrimRight(u.EscapedPath(), "/"), "/career") {
		return "", "", errors.New("invalid SuccessFactors XML origin")
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(q) != 3 || len(q["company"]) != 1 || len(q["career_ns"]) != 1 || len(q["resultType"]) != 1 || q.Get("career_ns") != "job_listing_summary" || q.Get("resultType") != "XML" {
		return "", "", errors.New("invalid SuccessFactors XML tenant")
	}
	company := strings.TrimSpace(q.Get("company"))
	if !sfXMLCompany.MatchString(company) {
		return "", "", errors.New("invalid SuccessFactors XML company")
	}
	return "https://" + strings.ToLower(u.Hostname()), company, nil
}

// Preserve ElementTree's direct-child lookup and leading text. Namespaced
// children and later duplicate siblings cannot replace the first plain child.
type rssXMLValue struct {
	text     string
	children map[string]rssXMLValue
}

func readRSSXMLValue(d *xml.Decoder, start xml.StartElement) (rssXMLValue, error) {
	v := rssXMLValue{children: map[string]rssXMLValue{}}
	leading := true
	var text strings.Builder
	for {
		token, err := d.Token()
		if err != nil {
			return v, err
		}
		switch x := token.(type) {
		case xml.CharData:
			if leading {
				text.Write(x)
			}
		case xml.StartElement:
			leading = false
			child, err := readRSSXMLValue(d, x)
			if err != nil {
				return v, err
			}
			if x.Name.Space == "" {
				if _, exists := v.children[x.Name.Local]; !exists {
					v.children[x.Name.Local] = child
				}
			}
		case xml.EndElement:
			if x.Name != start.Name {
				return v, errors.New("invalid XML child")
			}
			v.text = strings.TrimSpace(text.String())
			return v, nil
		}
	}
}

func parseSFLegacyXML(raw []byte, origin, company string) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	d := xml.NewDecoder(bytes.NewReader(raw))
	depth, roots := 0, 0
	for {
		token, err := d.Token()
		if err == io.EOF {
			if roots != 1 || depth != 0 {
				return RichDiscovery{}, errors.New("incomplete XML inventory")
			}
			return out, nil
		}
		if err != nil {
			return RichDiscovery{}, err
		}
		switch x := token.(type) {
		case xml.StartElement:
			if depth == 0 {
				roots++
				if roots != 1 {
					return RichDiscovery{}, errors.New("multiple XML roots")
				}
			}
			depth++
			if x.Name.Local != "Job" {
				continue
			}
			v, err := readRSSXMLValue(d, x)
			if err != nil {
				return RichDiscovery{}, err
			}
			depth--
			id, title := v.children["ReqId"].text, v.children["JobTitle"].text
			if id == "" || title == "" {
				continue
			}
			digits := true
			for _, r := range id {
				if !pythonRSSDigit(r) {
					digits = false
					break
				}
			}
			if !digits {
				continue
			}
			value := func(key string) string { return v.children[key].children["value"].text }
			location := []string{}
			for _, key := range []string{"filter8", "filter7"} {
				if s := value(key); s != "" {
					location = append(location, s)
				}
			}
			joined := strings.Join(location, ", ")
			if joined == "" {
				joined = value("filter6")
			}
			job := RichMonitorJob{URL: origin + "/sfcareer/jobreqcareer?jobId=" + url.QueryEscape(id) + "&company=" + url.QueryEscape(company), Title: &title, Metadata: map[string]any{"id": id}}
			if s := v.children["Job-Description"].text; s != "" {
				job.Description = &s
			}
			if joined != "" {
				job.Locations = []string{joined}
			}
			if kind := enrichment.NormalizeJobLocationType(value("filter4")); kind != "" {
				job.JobLocationType = kind
			}
			for key, field := range map[string]string{"category": "filter1", "travel_required": "filter2", "experience_level": "filter3", "site": "filter6", "approved_work_states": "mfield2"} {
				if s := value(field); s != "" {
					job.Metadata[key] = s
				}
			}
			out.Jobs = append(out.Jobs, job)
			if len(out.Jobs) == 50_000 {
				out.Truncated = true
				return out, nil
			}
		case xml.EndElement:
			depth--
		case xml.CharData:
			if depth == 0 && strings.TrimSpace(string(x)) != "" {
				return RichDiscovery{}, errors.New("XML data outside root")
			}
		}
	}
}

func parseGenericStructuredSummary(raw []byte) (RichDiscovery, error) {
	out, err := parseGenericRSS(raw)
	if err != nil {
		return out, err
	}
	for i := range out.Jobs {
		job := &out.Jobs[i]
		title, summary := "", ""
		if job.Title != nil {
			title = strings.Join(strings.Fields(html.UnescapeString(*job.Title)), " ")
		}
		if job.Description != nil {
			summary = strings.Join(strings.Fields(*job.Description), " ")
		}
		prefix := title + " | "
		if title == "" || !strings.HasPrefix(summary, prefix) {
			return RichDiscovery{}, errors.New("RSS structured summary title mismatch")
		}
		fields := strings.Split(strings.TrimPrefix(summary, prefix), " | ")
		if len(fields) < 1 || len(fields) > 2 {
			return RichDiscovery{}, errors.New("RSS structured summary field count")
		}
		for i := range fields {
			fields[i] = strings.TrimSpace(fields[i])
			if fields[i] == "" {
				return RichDiscovery{}, errors.New("RSS structured summary empty field")
			}
		}
		job.Title, job.Description = &title, nil
		job.Locations = []string{fields[len(fields)-1]}
		if len(fields) == 2 {
			job.EmploymentType = fields[0]
		}
	}
	return out, nil
}

func pythonRSSDigit(r rune) bool {
	return unicode.IsDigit(r) || strings.ContainsRune(pythonRSSOtherDigits, r)
}

// Python isdigit also accepts these non-decimal digits (Unicode 15.1.0).
const pythonRSSOtherDigits = "²³¹፩፪፫፬፭፮፯፰፱᧚⁰⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉①②③④⑤⑥⑦⑧⑨⑴⑵⑶⑷⑸⑹⑺⑻⑼⒈⒉⒊⒋⒌⒍⒎⒏⒐⓪⓵⓶⓷⓸⓹⓺⓻⓼⓽⓿❶❷❸❹❺❻❼❽❾➀➁➂➃➄➅➆➇➈➊➋➌➍➎➏➐➑➒𐩀𐩁𐩂𐩃𐹠𐹡𐹢𐹣𐹤𐹥𐹦𐹧𐹨𑁒𑁓𑁔𑁕𑁖𑁗𑁘𑁙𑁚🄀🄁🄂🄃🄄🄅🄆🄇🄈🄉🄊"
