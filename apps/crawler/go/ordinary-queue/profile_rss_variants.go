package queue

import (
	"errors"
	"net/url"
	"regexp"
	"strings"
)

var sfXMLCompany = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func SuccessFactorsLegacyXMLIdentity(source string) (string, string, error) {
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
