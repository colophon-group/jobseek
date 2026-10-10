package apisniffer

import (
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

// LastHTTPOptions binds the remaining session and authoritative-document
// providers to their original public identities; it grants no write authority.
type LastHTTPOptions struct {
	Provider, BoardURL, Origin, Dataarea, JobBoard, HROrganization, Site, Portal string
	Proxy                                                                        bool
}

var inforToken = regexp.MustCompile(`^[A-Za-z0-9._-]{1,128}$`)
var inforDataarea = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
var inforHost = regexp.MustCompile(`^(?:[a-z0-9-]{1,63}\.)+cloud\.infor\.com$`)
var peopleSoftPath = regexp.MustCompile(`(?i)^/psc/([A-Za-z0-9_-]{1,64})/([A-Za-z0-9_-]{1,64})/HRMS/c/HRS_HRAM_FL\.HRS_CG_SEARCH_FL\.GBL$`)
var peopleSoftID = regexp.MustCompile(`^[1-9][0-9]{0,11}$`)
var unisanteSlug = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
var papaJobPath = regexp.MustCompile(`(?i)^/job/[0-9]+/[a-z0-9][a-z0-9-]*/?$`)

func LastHTTPOptionsFromMetadata(provider, board, metadata string) (LastHTTPOptions, error) {
	o := LastHTTPOptions{Provider: provider, BoardURL: board}
	m, e := DecodeInlineMetadata(metadata)
	if e != nil {
		return o, e
	}
	u, e := url.Parse(board)
	if e != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Hostname() == "" || u.RawPath != "" {
		return o, ErrInventory
	}
	o.Origin = "https://" + strings.ToLower(u.Hostname())
	if p := u.Port(); p != "" && p != "443" {
		o.Origin += ":" + p
	}
	if v, ok := m["proxy"]; ok {
		var valid bool
		o.Proxy, valid = v.(bool)
		if !valid {
			return o, ErrInventory
		}
	}
	switch provider {
	case "papa_johns":
		if !strings.EqualFold(u.Hostname(), "jobs.papajohns.com") || u.Port() != "" && u.Port() != "443" || !strings.EqualFold(strings.TrimRight(u.Path, "/"), "/jobs") || u.RawQuery != "" || u.Fragment != "" {
			return o, ErrInventory
		}
	case "infor":
		if !inforHost.MatchString(strings.TrimRight(strings.ToLower(u.Hostname()), ".")) || u.Port() != "" && u.Port() != "443" && u.Port() != "1443" && u.Port() != "1444" || o.Proxy {
			return o, ErrInventory
		}
		o.Origin = "https://" + strings.TrimRight(strings.ToLower(u.Hostname()), ".")
		if p := u.Port(); p != "" && p != "443" {
			o.Origin += ":" + p
		}
		parts := []string{}
		for _, part := range strings.Split(u.Path, "/") {
			if part != "" {
				parts = append(parts, part)
			}
		}
		if len(parts) != 3 || !inforDataarea.MatchString(parts[0]) || parts[1] != "CandidateSelfService" || parts[2] != "lm" && parts[2] != "controller.servlet" {
			return o, ErrInventory
		}
		o.Dataarea = parts[0]
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil {
			return o, ErrInventory
		}
		single := func(k string) string {
			v := q[k]
			if len(v) != 1 {
				return ""
			}
			s := strings.TrimFunc(v[0], enterpriseSpace)
			if !inforToken.MatchString(s) {
				return ""
			}
			return s
		}
		o.JobBoard = single("context.session.key.JobBoard")
		o.HROrganization = single("context.session.key.HROrganization")
		if o.JobBoard == "" || o.HROrganization == "" {
			return o, ErrInventory
		}
		configured, exists := q["context.dataarea"]
		if !exists {
			configured, exists = q["dataarea"]
		}
		if exists && (len(configured) != 1 || configured[0] != o.Dataarea) {
			return o, ErrInventory
		}
		req, post := single("JobReq"), single("JobPost")
		if (req == "") != (post == "") {
			return o, ErrInventory
		}
		for k, want := range map[string]string{"origin": o.Origin, "dataarea": o.Dataarea, "job_board": o.JobBoard, "hr_organization": o.HROrganization} {
			if v, ok := m[k]; ok && v != nil && v != want {
				return o, ErrInventory
			}
		}
	case "peoplesoft":
		p := peopleSoftPath.FindStringSubmatch(u.Path)
		if len(p) != 3 || u.Port() != "" && u.Port() != "443" || u.Fragment != "" || o.Proxy {
			return o, ErrInventory
		}
		o.Site, o.Portal = p[1], p[2]
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil || len(q) > 6 {
			return o, ErrInventory
		}
		seen := map[string]bool{}
		for k, v := range q {
			key := strings.ToLower(k)
			if len(v) != 1 || seen[key] || key != "action" && key != "page" || key == "action" && !strings.EqualFold(v[0], "U") || key == "page" && !strings.EqualFold(v[0], "HRS_APP_SCHJOB_FL") {
				return o, ErrInventory
			}
			seen[key] = true
		}
	case "unisante":
		if board != "https://emploi.unisante.ch/index.php/offres" && board != "https://emploi.unisante.ch/offres" || o.Proxy {
			return o, ErrInventory
		}
	default:
		return o, ErrInventory
	}
	return o, nil
}
func (o LastHTTPOptions) Profile() string {
	switch o.Provider {
	case "papa_johns":
		if o.Proxy {
			return "papa_johns.proxy-listing-urls/v1"
		}
		return "papa_johns.listing-urls/v1"
	case "infor":
		return "infor.session-items/v1"
	case "peoplesoft":
		return "peoplesoft.session-items/v1"
	case "unisante":
		return "unisante.authoritative-items/v1"
	}
	return ""
}
func (o LastHTTPOptions) ListingURL() string {
	switch o.Provider {
	case "papa_johns":
		return "https://jobs.papajohns.com/jobs/"
	case "infor":
		return o.Origin + "/" + o.Dataarea + "/soapExt/ldrest/JobPosting/JobPostingListWebServices_ListOperation"
	case "peoplesoft":
		return o.Origin + o.PeopleSoftComponent() + "?Action=U&Page=HRS_APP_SCHJOB_FL"
	}
	return o.BoardURL
}
func (o LastHTTPOptions) PeopleSoftComponent() string {
	return "/psc/" + o.Site + "/" + o.Portal + "/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
}
func lastQuery(pairs [][2]string) string {
	values := make([]string, len(pairs))
	for n, p := range pairs {
		values[n] = url.QueryEscape(p[0]) + "=" + url.QueryEscape(p[1])
	}
	return strings.Join(values, "&")
}
func (o LastHTTPOptions) InforJobURL(req, post string) string {
	return o.Origin + "/" + o.Dataarea + "/CandidateSelfService/lm?" + lastQuery([][2]string{{"context.dataarea", o.Dataarea}, {"webappname", "CandidateSelfService"}, {"context.session.key.JobBoard", o.JobBoard}, {"context.session.key.HROrganization", o.HROrganization}, {"_saveKeys", "true"}, {"JobPost", post}, {"JobReq", req}, {"context.session.key.noheader", "true"}})
}
func (o LastHTTPOptions) PeopleSoftJobURL(id string) string {
	return o.Origin + o.PeopleSoftComponent() + "?Action=U&FOCUS=Applicant&JobOpeningId=" + id + "&Page=HRS_APP_JBPST_FL&PostingSeq=1&SiteId=1"
}
func (o LastHTTPOptions) ResourceMatches(raw string) bool {
	u, e := url.Parse(raw)
	if e != nil || u.User != nil || u.Fragment != "" || u.Opaque != "" || u.RawPath != "" || u.Scheme != "https" {
		return false
	}
	origin := "https://" + strings.ToLower(strings.TrimRight(u.Hostname(), "."))
	if port := u.Port(); port != "" && port != "443" {
		origin += ":" + port
	}
	if origin != o.Origin {
		return false
	}
	switch o.Provider {
	case "papa_johns":
		if u.Path != "/jobs/" {
			return false
		}
		q, e := url.ParseQuery(u.RawQuery)
		if e != nil || len(q) > 1 {
			return false
		}
		if len(q) == 0 {
			return true
		}
		v := q["page_jobs"]
		if len(v) != 1 {
			return false
		}
		n, e := strconv.Atoi(v[0])
		return e == nil && n >= 1 && n <= 1000
	case "infor":
		return u.Path == "/sso/SSOServlet" || u.Path == "/"+o.Dataarea+"/CandidateSelfService/lm" || u.Path == "/"+o.Dataarea+"/CandidateSelfService/controller.servlet" || u.Path == "/"+o.Dataarea+"/soapExt/ldrest/JobPosting/JobPostingListWebServices_ListOperation" || u.Path == "/"+o.Dataarea+"/soapExt/ldrest/JobPosting/Find_PostingDisplay_FormOperation"
	case "peoplesoft":
		return u.Path == o.PeopleSoftComponent() || u.Path == "/psp/"+o.Site+"/"+o.Portal+"/HRMS/"
	case "unisante":
		return raw == "https://emploi.unisante.ch/index.php/offres" || raw == "https://emploi.unisante.ch/offres" || strings.HasPrefix(u.Path, "/index.php/offre/") && u.RawQuery == "" && unisanteSlug.MatchString(strings.TrimPrefix(u.Path, "/index.php/offre/"))
	}
	return false
}
func lastPythonScalar(document *Document, v any) string {
	if !detailTruthy(v) {
		return ""
	}
	value, e := document.String(v)
	if e != nil {
		return ""
	}
	return strings.TrimFunc(value, enterpriseSpace)
}
