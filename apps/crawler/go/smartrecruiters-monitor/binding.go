package smartrecruiters

import (
	"net/url"
	"regexp"
	"strings"
)

// PublicationResourceMatches admits only a single bounded publication under
// the configured tenant. The monitor never follows outbound posting URLs.
func PublicationResourceMatches(token, resource string) bool {
	u, err := url.Parse(resource)
	if err != nil || u.Scheme != "https" || u.Host != "api.smartrecruiters.com" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" || u.Opaque != "" {
		return false
	}
	prefix := ListURL(token) + "/"
	return strings.HasPrefix(resource, prefix) && detailPathID.MatchString(strings.TrimPrefix(resource, prefix))
}

var locationIdentitySuffix = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/(?:geo|provider-codes)/[0-9a-f]{64}$`)

// IdentityMatches binds the already projected SDK result to the configured
// identity mode before the existing durable writer can accept it.
func (o Options) IdentityMatches(source, identity string) bool {
	if o.Template != nil {
		parts := strings.Split(*o.Template, "{job_id}")
		if identity != "" || len(parts) != 2 || !strings.HasPrefix(source, parts[0]) || !strings.HasSuffix(source, parts[1]) || len(source) < len(parts[0])+len(parts[1]) {
			return false
		}
		id := source[len(parts[0]) : len(source)-len(parts[1])]
		return uuidRE.MatchString(id) && id == strings.ToLower(id)
	}
	u, err := url.Parse(source)
	prefix := "https://jobs.smartrecruiters.com/" + o.Token + "/"
	if err != nil || u.Scheme != "https" || u.Host != "jobs.smartrecruiters.com" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" || !strings.HasPrefix(source, prefix) || !detailPathID.MatchString(strings.TrimPrefix(source, prefix)) {
		return false
	}
	identityPrefix := "smartrecruiters:" + strings.ToLower(o.Token) + ":"
	if !strings.HasPrefix(identity, identityPrefix) || !sourceIdentityRE.MatchString(identity) {
		return false
	}
	id := strings.TrimPrefix(identity, identityPrefix)
	if o.Identity == "job-v1" {
		return uuidRE.MatchString(id) && id == strings.ToLower(id)
	}
	return o.Identity == "job-location-v1" && locationIdentitySuffix.MatchString(id)
}
