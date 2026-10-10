package apisniffer

import (
	"context"
	"html"
	"net/http"
	"net/url"
	"regexp"
	"strings"

	"github.com/andybalholm/cascadia"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	xhtml "golang.org/x/net/html"
)

type LastHTTPDetailOptions struct {
	LastHTTPOptions
	SourceURL, Endpoint, ID, Requisition, Posting string
}

func LastHTTPDetailOptionsFromConfig(provider, board, source, raw string) (LastHTTPDetailOptions, error) {
	base, e := LastHTTPOptionsFromMetadata(provider, board, "{}")
	o := LastHTTPDetailOptions{LastHTTPOptions: base, SourceURL: source}
	if e != nil || provider != "infor" && provider != "peoplesoft" {
		return o, ErrOptions
	}
	if strings.TrimSpace(raw) == "" {
		raw = "{}"
	}
	md, e := DecodeInlineMetadata(raw)
	if e != nil {
		return o, e
	}
	for k := range md {
		if k != "enrich" {
			return o, ErrOptions
		}
	}
	u, e := url.Parse(source)
	if e != nil || u.User != nil || u.Scheme != "https" || u.Fragment != "" || u.Opaque != "" || u.RawPath != "" || len(source) > 8192 || strings.ContainsAny(source, "\x00\r\n") {
		return o, ErrOptions
	}
	if provider == "infor" {
		detail, e := LastHTTPOptionsFromMetadata(provider, source, "{}")
		if e != nil || detail.Origin != base.Origin || detail.Dataarea != base.Dataarea || detail.JobBoard != base.JobBoard || detail.HROrganization != base.HROrganization {
			return o, ErrOptions
		}
		q := u.Query()
		if len(q["JobReq"]) != 1 || len(q["JobPost"]) != 1 {
			return o, ErrOptions
		}
		o.Requisition = strings.TrimFunc(q.Get("JobReq"), enterpriseSpace)
		o.Posting = strings.TrimFunc(q.Get("JobPost"), enterpriseSpace)
		if !inforToken.MatchString(o.Requisition) || !inforToken.MatchString(o.Posting) {
			return o, ErrOptions
		}
		o.Endpoint = base.Origin + "/" + base.Dataarea + "/soapExt/ldrest/JobPosting/Find_PostingDisplay_FormOperation"
	} else {
		if u.Scheme+"://"+u.Host != base.Origin || !strings.EqualFold(u.Path, base.PeopleSoftComponent()) {
			return o, ErrOptions
		}
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil {
			return o, ErrOptions
		}
		params := map[string]string{}
		pairs := 0
		for k, values := range q {
			pairs += len(values)
			key := strings.ToLower(k)
			if _, ok := params[key]; ok || len(values) != 1 {
				return o, ErrOptions
			}
			params[key] = values[0]
		}
		if pairs > 12 {
			return o, ErrOptions
		}
		o.ID = params["jobopeningid"]
		if !peopleSoftID.MatchString(o.ID) || params["action"] != "" && !strings.EqualFold(params["action"], "U") || !strings.EqualFold(params["page"], "HRS_APP_JBPST_FL") || params["postingseq"] != "" && params["postingseq"] != "1" || params["siteid"] != "" && params["siteid"] != "1" {
			return o, ErrOptions
		}
		o.Endpoint = base.PeopleSoftJobURL(o.ID)
	}
	return o, nil
}
func (o LastHTTPDetailOptions) Profile() string { return o.Provider + ".session-detail/v1" }
func (o LastHTTPDetailOptions) ResourceMatches(raw string) bool {
	if !o.LastHTTPOptions.ResourceMatches(raw) {
		return false
	}
	u, _ := url.Parse(raw)
	if o.Provider == "infor" {
		return u.Path == "/sso/SSOServlet" || u.Path == "/"+o.Dataarea+"/CandidateSelfService/lm" || u.Path == "/"+o.Dataarea+"/CandidateSelfService/controller.servlet" || u.Path == "/"+o.Dataarea+"/soapExt/ldrest/JobPosting/Find_PostingDisplay_FormOperation"
	}
	return raw == o.Endpoint || u.Path == "/psp/"+o.Site+"/"+o.Portal+"/HRMS/"
}
func ParseInforDetail(body []byte) (map[string]any, error) {
	d, e := Decode(body)
	if e != nil {
		return nil, e
	}
	root, ok := d.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	row, ok := root["Find_PostingDisplay_FormOperationResponse"].(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	choose := func(a, b string) any {
		if detailTruthy(row[a]) {
			return row[a]
		}
		return row[b]
	}
	text := func(v any) any {
		if s, ok := v.(string); ok {
			if s = strings.TrimFunc(s, enterpriseSpace); s != "" {
				return s
			}
		}
		return nil
	}
	posted, e := inforDate(row["PostingDateRange.Begin"])
	if e != nil {
		return nil, e
	}
	var date any
	if posted != nil {
		date = *posted
	}
	metadata := map[string]any{}
	for k, target := range map[string]string{"JobRequisition": "job_requisition", "JobPosting": "job_posting", "Category.Description": "category", "RelationshipToOrganization.Description": "relationship", "JobRequisitionLocation": "job_requisition_location"} {
		if v := text(row[k]); v != nil {
			metadata[target] = v
		}
	}
	out := map[string]any{"title": text(choose("__Description_translation___", "Description")), "description": text(choose("__PositionDescription_translation___", "PositionDescription")), "locations": inforLocation(choose("LocationOfJob.Description", "__LocationOfJob.Description_translation___")), "date_posted": date, "language": "en"}
	if len(metadata) > 0 {
		out["metadata"] = metadata
	}
	return out, nil
}
func FetchInforDetail(ctx context.Context, o LastHTTPDetailOptions, fetch SuccessFactorsLegacyFetch) (map[string]any, error) {
	if o.Provider != "infor" || fetch == nil {
		return nil, ErrOptions
	}
	_, h, e := fetch(ctx, Request{Method: "GET", URL: o.SourceURL})
	if e != nil {
		return nil, e
	}
	headers, e := inforSessionHeaders(h)
	if e != nil {
		return nil, e
	}
	params := [][2]string{{"JobPosting", o.Posting}, {"JobRequisition", o.Requisition}}
	for _, k := range []string{"Description", "__Description_translation___", "PositionDescription", "__PositionDescription_translation___", "LocationOfJob.Description", "__LocationOfJob.Description_translation___", "Category.Description", "RelationshipToOrganization.Description", "PostingDateRange.Begin", "PostingDateRange.End", "SalaryRangeAmount", "SalaryRange.BeginningPay", "SalaryRange.EndingPay", "SalaryEntered", "StatusSwitchForCandidateSpace", "SalaryRange.PayRangeCurrencyCode", "Live", "JobRequisitionLocation", "JobRequisitionConsentAgreement", "JobRequisitionAcknowledgement", "JobRequisitionSelfIdConfiguration", "JobRequisitionShowDependents", "JobReqTSAssessment"} {
		params = append(params, [2]string{k, " "})
	}
	params = append(params, [2]string{"csk.IsoLocale", "en"}, [2]string{"HROrganization", o.HROrganization}, [2]string{"JobRequisitionApplicationProcessEntered", " "})
	body, _, e := fetch(ctx, Request{Method: "GET", URL: o.Endpoint + "?" + lastQuery(params), Headers: headers})
	if e != nil {
		return nil, e
	}
	return ParseInforDetail(body)
}

var peopleSoftWorkplace = regexp.MustCompile(`(?i)\b(remote|hybrid|on[ -]?site)\b`)

func ParsePeopleSoftDetail(source, expectedID string, employment func(string) any, workplace func(string) any, salary func(string) (any, error)) (map[string]any, error) {
	if employment == nil || workplace == nil || salary == nil {
		return nil, ErrOptions
	}
	doc, e := xhtml.ParseWithOptions(strings.NewReader(source), xhtml.ParseOptionEnableScripting(false))
	if e != nil {
		return nil, e
	}
	value := func(selector string) string {
		n := cascadia.Query(doc, cascadia.MustCompile(selector))
		if n == nil {
			return ""
		}
		return strings.Join(strings.FieldsFunc(paylocityText(n, ""), enterpriseSpace), " ")
	}
	id, title := value("#HRS_SCH_WRK2_HRS_JOB_OPENING_ID"), value("#HRS_SCH_WRK2_POSTING_TITLE")
	if id == "" || title == "" || expectedID != "" && id != expectedID {
		return nil, ErrInventory
	}
	sections, qualifications, responsibilities := []string{}, []string{}, []string{}
	salaryText := ""
	for _, row := range cascadia.QueryAll(doc, cascadia.MustCompile(`div[id^='win0divHRS_SCH_PSTDSC_row$']`)) {
		headingNode := cascadia.Query(row, cascadia.MustCompile("h2 .ps-text"))
		contentNode := cascadia.Query(row, cascadia.MustCompile(`span[id^='HRS_SCH_PSTDSC_DESCRLONG$']`))
		if headingNode == nil || contentNode == nil {
			continue
		}
		heading := strings.Join(strings.FieldsFunc(paylocityText(headingNode, ""), enterpriseSpace), " ")
		content, e := dom.InnerHTML(contentNode)
		if e != nil {
			return nil, e
		}
		content = strings.TrimFunc(content, enterpriseSpace)
		if heading == "" || content == "" {
			continue
		}
		escaped := strings.NewReplacer("&#39;", "&#x27;", "&#34;", "&quot;").Replace(html.EscapeString(heading))
		section := "<h2>" + escaped + "</h2>\n" + content
		sections = append(sections, section)
		fold := strings.ToLower(heading)
		if strings.Contains(fold, "qualification") {
			qualifications = append(qualifications, section)
		}
		if strings.Contains(fold, "what your job will be like") || strings.Contains(fold, "responsibilit") {
			responsibilities = append(responsibilities, section)
		}
		if strings.Contains(fold, "salary") {
			salaryText = paylocityText(contentNode, "")
		}
	}
	if len(sections) == 0 {
		return nil, ErrInventory
	}
	extras := map[string]any{}
	if len(qualifications) > 0 {
		extras["qualifications"] = strings.Join(qualifications, "\n")
	}
	if len(responsibilities) > 0 {
		extras["responsibilities"] = strings.Join(responsibilities, "\n")
	}
	metadata := map[string]any{"job_id": id}
	if v := value("#HRS_SCH_WRK_HRS_REG_TEMP"); v != "" {
		metadata["regular_or_temporary"] = v
	}
	var location any
	if v := value("#HRS_SCH_WRK_HRS_DESCRLONG"); v != "" {
		location = []string{v}
	}
	var rawWorkplace string
	if m := peopleSoftWorkplace.FindStringSubmatch(title); len(m) == 2 {
		rawWorkplace = m[1]
	}
	var parsedSalary any
	if salaryText != "" {
		parsedSalary, e = salary(salaryText + " per year")
		if e != nil {
			return nil, e
		}
	}
	out := map[string]any{"title": title, "description": strings.Join(sections, "\n"), "locations": location, "employment_type": employment(value("#HRS_SCH_WRK_HRS_FULL_PART_TIME")), "job_location_type": workplace(rawWorkplace), "base_salary": parsedSalary, "metadata": metadata}
	if len(extras) > 0 {
		out["extras"] = extras
	}
	return out, nil
}
func FetchPeopleSoftDetail(ctx context.Context, o LastHTTPDetailOptions, fetch SmallProviderFetch, employment func(string) any, workplace func(string) any, salary func(string) (any, error)) (map[string]any, error) {
	if o.Provider != "peoplesoft" || fetch == nil {
		return nil, ErrOptions
	}
	if _, e := fetch(ctx, Request{Method: http.MethodGet, URL: o.Origin + "/psp/" + o.Site + "/" + o.Portal + "/HRMS/?cmd=logout"}); e != nil {
		return nil, e
	}
	body, e := fetch(ctx, Request{Method: http.MethodGet, URL: o.Endpoint})
	if e != nil {
		return nil, e
	}
	return ParsePeopleSoftDetail(string(body), o.ID, employment, workplace, salary)
}
