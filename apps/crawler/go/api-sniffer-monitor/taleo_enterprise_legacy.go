package apisniffer

import (
	"context"
	"encoding/json"
	stdhtml "html"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

var enterpriseLegacyListPath = regexp.MustCompile(`^/careersection/[a-z0-9_-]{1,64}/(?:joblist|jobsearch)\.ftl$`)
var enterpriseLegacyListMarker = regexp.MustCompile(`api\.fillList\(\s*['"]requisitionListInterface['"]\s*,\s*['"]listRequisition['"]\s*,\s*`)
var enterpriseLegacyTotal = regexp.MustCompile(`\b([0-9]{1,5}) jobs? found\b`)
var enterpriseLegacyID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// TaleoEnterpriseLegacyBoard is an exact public Enterprise career section.
// This is separate from the Business Edition (*.tbe.taleo.net) identity.
type TaleoEnterpriseLegacyBoard struct {
	Listing string
	Detail  string
}

func TaleoEnterpriseLegacyFromURL(raw string) (TaleoEnterpriseLegacyBoard, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Port() != "" {
		return TaleoEnterpriseLegacyBoard{}, ErrOptions
	}
	host := strings.ToLower(u.Hostname())
	if !strings.HasSuffix(host, ".taleo.net") || strings.HasSuffix(host, ".tbe.taleo.net") || u.EscapedPath() != u.Path || !enterpriseLegacyListPath.MatchString(u.Path) {
		return TaleoEnterpriseLegacyBoard{}, ErrOptions
	}
	q := u.Query()
	if len(q["lang"]) != 1 || q.Get("lang") != "en" || len(q) > 2 || len(q["portal"]) > 1 || len(q["portal"]) == 1 && (len(q["portal"][0]) == 0 || len(q["portal"][0]) > 64) {
		return TaleoEnterpriseLegacyBoard{}, ErrOptions
	}
	for key := range q {
		if key != "lang" && key != "portal" {
			return TaleoEnterpriseLegacyBoard{}, ErrOptions
		}
	}
	if strings.HasSuffix(u.Path, "/joblist.ftl") && len(q) != 1 {
		return TaleoEnterpriseLegacyBoard{}, ErrOptions
	}
	path := strings.TrimSuffix(strings.TrimSuffix(u.EscapedPath(), "joblist.ftl"), "jobsearch.ftl")
	listing := "https://" + host + path + "joblist.ftl?lang=en"
	detail := "https://" + host + path + "jobdetail.ftl?job="
	return TaleoEnterpriseLegacyBoard{Listing: listing, Detail: detail}, nil
}

func TaleoEnterpriseLegacyFromMetadata(raw, metadata string) (TaleoEnterpriseLegacyBoard, error) {
	board, err := TaleoEnterpriseLegacyFromURL(raw)
	if err != nil {
		return board, err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal([]byte(metadata), &fields); err != nil {
		return TaleoEnterpriseLegacyBoard{}, ErrOptions
	}
	if value, ok := fields["enterprise_section"]; ok {
		var section string
		if json.Unmarshal(value, &section) != nil || section != board.Listing {
			return TaleoEnterpriseLegacyBoard{}, ErrOptions
		}
	}
	return board, nil
}

func (b TaleoEnterpriseLegacyBoard) ResourceMatches(raw string) bool { return raw == b.Listing }

// ParseTaleoEnterpriseLegacyListing reads the rendered page's bounded
// fillList data. A multi-page result fails closed until its public pager is
// qualified; the current Jumeirah section exposes all 19 jobs on one page.
func ParseTaleoEnterpriseLegacyListing(source string, board TaleoEnterpriseLegacyBoard) ([]Job, error) {
	if board.Listing == "" || len(source) > 2_000_000 {
		return nil, ErrInventory
	}
	match := enterpriseLegacyTotal.FindStringSubmatch(source)
	if match == nil {
		return nil, ErrInventory
	}
	total, err := strconv.Atoi(match[1])
	if err != nil || total > 10_000 {
		return nil, ErrInventory
	}
	values, err := parseEnterpriseFillList(source, enterpriseLegacyListMarker, 43*100)
	if err != nil || len(values)%43 != 0 || len(values)/43 != total {
		return nil, ErrInventory
	}
	jobs := make([]Job, 0, total)
	seen := make(map[string]bool, total)
	for start := 0; start < len(values); start += 43 {
		row := values[start : start+43]
		id := inlineTrim(row[3])
		requisition := inlineTrim(row[12])
		title := inlineTrim(stdhtml.UnescapeString(enterpriseUnquote(row[4])))
		if !enterpriseLegacyID.MatchString(id) || !enterpriseLegacyID.MatchString(requisition) || title == "" || seen[requisition] {
			return nil, ErrInventory
		}
		seen[requisition] = true
		job := Job{URL: board.Detail + url.QueryEscape(requisition) + "&lang=en", Title: title, Metadata: map[string]any{"ats_job_id": id, "requisition_number": requisition}}
		if location := inlineTrim(stdhtml.UnescapeString(row[13])); location != "" {
			job.Locations = []string{location}
		}
		if date := inlineTrim(stdhtml.UnescapeString(row[20])); enterpriseDate(date) {
			job.DatePosted = date
		}
		jobs = append(jobs, job)
	}
	return jobs, nil
}

func DiscoverTaleoEnterpriseLegacy(ctx context.Context, board TaleoEnterpriseLegacyBoard, fetch func(context.Context, string) ([]byte, error)) ([]Job, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	page, err := fetch(ctx, board.Listing)
	if err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return ParseTaleoEnterpriseLegacyListing(string(page), board)
}
