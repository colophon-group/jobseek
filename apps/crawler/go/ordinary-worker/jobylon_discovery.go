package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/cookiejar"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

var jobylonJobsAnchor = regexp.MustCompile(`JBL\.embed_v2\['jobs'\]\s*=\s*\[`)
var jobylonBareKey = regexp.MustCompile(`^([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:`)
var jobylonTrailingComma = regexp.MustCompile(`,(\s*[}\]])`)
var jobylonLanguage = regexp.MustCompile(`^job-lang-([a-z]{2})`)
var jobylonDMY = regexp.MustCompile(`(\p{Nd}{1,2})[.\s]+([A-Za-zÀ-ž]+)[.,\s]+(\p{Nd}{4})`)
var jobylonMDY = regexp.MustCompile(`([A-Za-zÀ-ž]+)\s+(\p{Nd}{1,2})[.,\s]+(\p{Nd}{4})`)

// One bounded public document request, retaining the legacy redirect behavior.
// Queue retry and lifecycle authority stay in the existing claim consumer.
func discoverJobylonInventory(ctx context.Context, verified *http.Client, p queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if verified == nil || p.Provider != "jobylon" || p.Profile != "jobylon.embed-items/v1" || p.Endpoint != "https://cdn.jobylon.com/jobs/"+p.Token+"/embed/v2/" {
		return result, queue.ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client := *verified
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	client.Jar, _ = cookiejar.New(nil)
	doc, err := jsonld.FetchDocumentWithClient(ctx, p.Endpoint, jsonld.DocumentOptions{}, &client)
	if doc.Responses > 0 {
		var policy *string
		if doc.TDMPolicy != "" {
			policy = &doc.TDMPolicy
		}
		result.Response = &GreenhouseResponse{endpoint: p.Endpoint, finalURL: doc.FinalURL, status: doc.Status, bytes: doc.Bytes, reserved: doc.ErrorKind == "tdm", policy: policy, reservationSource: doc.TDMSource}
	}
	if err != nil {
		return result, err
	}
	if doc.Status < 200 || doc.Status >= 300 {
		return result, &DiscoveryError{Kind: "http_status", Status: doc.Status}
	}
	jobs, truncated, err := parseJobylonInventory(jsonld.DecodeDocument(doc.Body, doc.ContentType))
	if err != nil {
		return result, &DiscoveryError{Kind: "invalid_inventory", cause: err}
	}
	result.Jobs, result.Truncated = jobs, truncated
	return result, nil
}

// This is a lexical translation of the Python monitor's narrow object-literal
// subset. It never evaluates JavaScript. Missing or malformed blocks are the
// legacy empty inventory, which still passes through the shared drop guard.
func jobylonJSON(source string) []byte {
	match := jobylonJobsAnchor.FindStringIndex(source)
	if match == nil {
		return nil
	}
	start, end, depth := match[1]-1, -1, 0
	var quote byte
	for i := start; i < len(source); i++ {
		ch := source[i]
		if quote != 0 {
			if ch == '\\' && i+1 < len(source) {
				i++
			} else if ch == quote {
				quote = 0
			}
			continue
		}
		if ch == '\'' || ch == '"' {
			quote = ch
		} else if ch == '[' {
			depth++
		} else if ch == ']' {
			depth--
			if depth == 0 {
				end = i + 1
				break
			}
		}
	}
	if end < 0 {
		return nil
	}
	raw := source[start:end]
	var converted strings.Builder
	double := false
	for i := 0; i < len(raw); i++ {
		ch := raw[i]
		if ch == '\\' && double && i+1 < len(raw) {
			converted.WriteString(raw[i : i+2])
			i++
			continue
		}
		if ch == '"' {
			double = !double
		} else if ch == '\'' && !double {
			j := i + 1
			for j < len(raw) && raw[j] != '\'' {
				if raw[j] == '\\' && j+1 < len(raw) {
					j++
				}
				j++
			}
			body := raw[i+1 : j]
			decoded := body
			if strings.Contains(body, `\`) {
				var candidate string
				if json.Unmarshal([]byte(`"`+strings.ReplaceAll(body, `"`, `\"`)+`"`), &candidate) == nil {
					decoded = candidate
				}
			}
			encoded, _ := json.Marshal(decoded)
			converted.Write(encoded)
			i = j
			continue
		}
		converted.WriteByte(ch)
	}
	raw = converted.String()
	var keys strings.Builder
	double = false
	for i := 0; i < len(raw); i++ {
		ch := raw[i]
		if double && ch == '\\' && i+1 < len(raw) {
			keys.WriteString(raw[i : i+2])
			i++
			continue
		}
		if ch == '"' {
			double = !double
		} else if !double && (ch == '{' || ch == ',') {
			if m := jobylonBareKey.FindStringSubmatchIndex(raw[i:]); m != nil {
				keys.WriteString(raw[i+m[2] : i+m[3]])
				keys.WriteByte('"')
				keys.WriteString(raw[i+m[4] : i+m[5]])
				keys.WriteString(`":`)
				i += m[1] - 1
				continue
			}
		}
		keys.WriteByte(ch)
	}
	// Preserve Python's global trailing-comma replacement, including its
	// behavior inside string values, instead of changing provider semantics.
	return []byte(jobylonTrailingComma.ReplaceAllString(keys.String(), "$1"))
}

func parseJobylonInventory(source string) ([]RichMonitorJob, bool, error) {
	jobs := []RichMonitorJob{}
	doc, err := apisniffer.Decode(jobylonJSON(source))
	if err != nil {
		return jobs, false, nil
	}
	rows, ok := doc.Value.([]any)
	if !ok {
		return jobs, false, nil
	}
	seen := map[string]bool{}
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			return nil, false, queue.ErrConfiguration
		}
		if !gemFieldTruthy(row["url"]) || !gemFieldTruthy(row["id"]) {
			continue
		}
		path, err := doc.String(row["url"])
		if err != nil {
			return nil, false, err
		}
		if !strings.HasPrefix(path, "http://") && !strings.HasPrefix(path, "https://") {
			if !strings.HasPrefix(path, "/") {
				path = "/" + path
			}
			path = "https://emp.jobylon.com" + path
		}
		job := RichMonitorJob{URL: path, Metadata: map[string]any{}}
		if title, ok := row["title"].(string); ok {
			job.Title = &title
		}
		id, err := doc.String(row["id"])
		if err != nil {
			return nil, false, err
		}
		job.Metadata["id"] = id
		for _, key := range []string{"company_id", "company", "function", "experience", "employment_type", "workspace", "to_date", "published_date"} {
			if text, ok := row[key].(string); ok && text != "" && text != "None" {
				target := key
				if key == "employment_type" {
					target = "employment_type_label"
				}
				if key == "published_date" {
					target = "published_date_raw"
				}
				job.Metadata[target] = text
			}
		}
		clean := func(value any) []string {
			var result []string
			if values, ok := value.([]any); ok {
				for _, value := range values {
					if text, ok := value.(string); ok && strings.TrimSpace(text) != "" {
						result = append(result, strings.TrimSpace(text))
					}
				}
			}
			return result
		}
		if departments := clean(row["departments"]); len(departments) > 0 {
			job.Metadata["departments"] = departments
		}
		job.Locations = clean(row["locations"])
		if len(job.Locations) == 0 {
			if text, ok := row["locations_text"].(string); ok && strings.TrimSpace(text) != "" {
				job.Locations = []string{strings.TrimSpace(text)}
			}
		}
		if workspace, ok := row["workspace"].(string); ok {
			workspace = strings.ToLower(strings.TrimSpace(workspace))
			for _, token := range []string{"remote", "distans", "etätyö", "hjemmefra", "fjernarbejde"} {
				if strings.Contains(workspace, token) {
					job.JobLocationType = "TELECOMMUTE"
					break
				}
			}
		}
		if klass, ok := row["klass"].(map[string]any); ok {
			for _, key := range doc.ObjectKeys(klass) {
				if m := jobylonLanguage.FindStringSubmatch(key); m != nil {
					job.Language = m[1]
					break
				}
			}
		}
		if gemFieldTruthy(row["published_date"]) {
			date, ok := row["published_date"].(string)
			if !ok {
				return nil, false, queue.ErrConfiguration
			}
			job.DatePosted = jobylonDate(date)
		}
		// Python parses before deduplication: a malformed duplicate still fails.
		if seen[path] {
			continue
		}
		seen[path] = true
		jobs = append(jobs, job)
		if len(jobs) >= 50_000 {
			return jobs, true, nil
		}
	}
	return jobs, false, nil
}

func jobylonDate(value string) any {
	value = strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) {
			return ' '
		}
		return r
	}, value)
	m := jobylonDMY.FindStringSubmatch(strings.TrimSpace(value))
	if m == nil {
		m = jobylonMDY.FindStringSubmatch(strings.TrimSpace(value))
		if m == nil {
			return nil
		}
		m[1], m[2] = m[2], m[1]
	}
	day, err := strconv.Atoi(jobylonDigits(m[1]))
	if err != nil {
		return nil
	}
	year, err := strconv.Atoi(jobylonDigits(m[3]))
	if err != nil || year == 0 {
		return nil
	}
	month := 0
	for _, names := range []string{
		"january february march april may june july august september october november december",
		"januari februari mars april maj juni juli augusti september oktober november december",
		"januar februar mars april mai juni juli august september oktober november desember",
		"januar februar marts april maj juni juli august september oktober november december",
		"tammikuuta helmikuuta maaliskuuta huhtikuuta toukokuuta kesäkuuta heinäkuuta elokuuta syyskuuta lokakuuta marraskuuta joulukuuta",
	} {
		for i, name := range strings.Fields(names) {
			if name == strings.ToLower(m[2]) {
				month = i + 1
				break
			}
		}
		if month > 0 {
			break
		}
	}
	if month == 0 {
		return nil
	}
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if date.Day() != day || int(date.Month()) != month || date.Year() != year {
		return nil
	}
	return fmt.Sprintf("%04d-%02d-%02d", year, month, day)
}

func jobylonDigits(value string) string {
	return strings.Map(func(r rune) rune {
		for _, span := range unicode.Nd.R16 {
			if r >= rune(span.Lo) && r <= rune(span.Hi) && (r-rune(span.Lo))%rune(span.Stride) == 0 {
				return '0' + (r-rune(span.Lo))/rune(span.Stride)%10
			}
		}
		for _, span := range unicode.Nd.R32 {
			if r >= rune(span.Lo) && r <= rune(span.Hi) && (r-rune(span.Lo))%rune(span.Stride) == 0 {
				return '0' + (r-rune(span.Lo))/rune(span.Stride)%10
			}
		}
		return r
	}, value)
}
