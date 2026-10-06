package apisniffer

import (
	"html"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type MokahrDetailRoute struct {
	Partition MokahrPartition
	JobID     string
}

var mokahrJobFragment = regexp.MustCompile(`^/?job/([A-Za-z0-9_-]{1,128})/?$`)

func MokahrDetailRouteForSource(source string) (MokahrDetailRoute, error) {
	u, e := url.Parse(source)
	if e != nil || u.RawPath != "" || u.RawFragment != "" {
		return MokahrDetailRoute{}, ErrOptions
	}
	match := mokahrJobFragment.FindStringSubmatch(u.Fragment)
	if len(match) != 2 {
		return MokahrDetailRoute{}, ErrOptions
	}
	segments := []string{}
	for _, v := range strings.Split(u.Path, "/") {
		if v != "" {
			segments = append(segments, v)
		}
	}
	if len(segments) != 3 || !mokahrRoute.MatchString(segments[0]) || !mokahrID.MatchString(segments[1]) {
		return MokahrDetailRoute{}, ErrOptions
	}
	site, ok := mokahrSite(segments[2])
	if !ok {
		return MokahrDetailRoute{}, ErrOptions
	}
	p, e := mokahrPartition(source, segments[1], site, true)
	if e != nil {
		return MokahrDetailRoute{}, e
	}
	return MokahrDetailRoute{p, match[1]}, nil
}
func (r MokahrDetailRoute) FallbackPartition() MokahrPartition {
	p := r.Partition
	path := "social-recruitment"
	if strings.Contains(p.Path, "social") {
		path = "campus-recruitment"
	}
	p.Path = path
	p.PageURL = p.Origin + "/" + path + "/" + p.OrgID + "/" + strconv.Itoa(p.SiteID)
	return p
}
func (r MokahrDetailRoute) APIURL() string {
	return r.Partition.Origin + "/api/outer/ats-apply/website/job"
}
func (r MokahrDetailRoute) ResourceMatches(source string) bool {
	return source == r.Partition.PageURL || source == r.FallbackPartition().PageURL || source == r.APIURL()
}

// The older detail bootstrap can omit organization/site labels. Its resources
// and POST arguments remain bound to the canonical route; contradictory labels
// fail rather than silently selecting another tenant's payload.
func MokahrDetailBootstrap(page string, p MokahrPartition) (string, map[int]string, error) {
	m := mokahrInit.FindStringSubmatch(page)
	if len(m) != 2 {
		return "", nil, ErrInventory
	}
	d, e := Decode([]byte(html.UnescapeString(m[1])))
	if e != nil {
		return "", nil, e
	}
	value, ok := d.Value.(map[string]any)
	if !ok {
		return "", nil, ErrInventory
	}
	iv, _ := value["aesIv"].(string)
	if iv == "" {
		return "", nil, ErrInventory
	}
	if org, ok := value["org"].(map[string]any); ok {
		if id, present := org["id"]; present && id != p.OrgID {
			return "", nil, ErrInventory
		}
		if site, present := org["siteId"]; present {
			n, ok := mokahrSite(site)
			if !ok || n != p.SiteID {
				return "", nil, ErrInventory
			}
		}
	}
	if site, present := value["siteId"]; present {
		n, ok := mokahrSite(site)
		if !ok || n != p.SiteID {
			return "", nil, ErrInventory
		}
	}
	return iv, mokahrCities(value), nil
}
