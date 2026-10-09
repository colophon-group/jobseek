package apisniffer

import (
	"context"
	"errors"
	"html"
	"net/url"
	"regexp"
	"strings"
	"time"

	xhtml "golang.org/x/net/html"
	"golang.org/x/text/cases"
)

var ErrBrassRingSnapshot = errors.New("BrassRing snapshot changed")

type BrassRingPageLoader func(context.Context, int, bool) (*Document, error)

// Loader owns the existing browser's empty-search/sort/next-page actions and
// committed-page barriers; this pure collector grants no browser authority.
func DiscoverBrassRing(ctx context.Context, b BrassRingBoard, load BrassRingPageLoader, hydrate func(context.Context, Job) ([]string, error), normalize SmallDescriptionNormalizer) (Inventory, error) {
	empty := Inventory{Jobs: []Job{}}
	if ctx.Err() != nil {
		return empty, ctx.Err()
	}
	if load == nil || normalize == nil {
		return empty, ErrOptions
	}
	first, e := load(ctx, 1, false)
	if e != nil {
		return empty, e
	}
	total, rows, e := BrassRingPage(first)
	if e != nil {
		return empty, e
	}
	expected := min(total, 50000)
	if expected > 0 && len(rows) == 0 {
		return empty, ErrBrassRingSnapshot
	}
	size := len(rows)
	if size == 0 {
		size = 50
	}
	pages := (expected + size - 1) / size
	if pages > 1 {
		sorted, e := load(ctx, 1, true)
		if e != nil {
			return empty, e
		}
		n, r, e := BrassRingPage(sorted)
		if e != nil {
			return empty, e
		}
		if total > 0 && n == 0 {
			return empty, ErrBrassRingSnapshot
		}
		total, rows = n, r
		expected = min(total, 50000)
		if expected > 0 && len(rows) == 0 {
			return empty, ErrBrassRingSnapshot
		}
		size = len(rows)
		if size == 0 {
			size = 50
		}
		pages = (expected + size - 1) / size
	}
	for page := 2; page <= pages; page++ {
		if ctx.Err() != nil {
			return empty, ctx.Err()
		}
		d, e := load(ctx, page, false)
		if e != nil {
			return empty, e
		}
		n, r, e := BrassRingPage(d)
		if e != nil {
			return empty, e
		}
		if n != total || len(rows)+len(r) > 100000 {
			return empty, ErrBrassRingSnapshot
		}
		rows = append(rows, r...)
	}
	truncated := total > 50000
	if len(rows) < expected || !truncated && len(rows) != expected {
		return empty, ErrBrassRingSnapshot
	}
	rows = rows[:expected]
	jobs := []Job{}
	seen := map[string]bool{}
	for _, r := range rows {
		j, valid, e := BrassRingJob(r, b, normalize)
		if e != nil {
			return empty, e
		}
		if !valid {
			return empty, ErrBrassRingSnapshot
		}
		id, _ := j.Metadata["requisition_id"].(string)
		if id == "" || seen[id] {
			return empty, ErrBrassRingSnapshot
		}
		seen[id] = true
		if len(j.Locations) == 0 {
			if hydrate == nil {
				return empty, ErrInventory
			}
			j.Locations, e = hydrate(ctx, j)
			if e != nil {
				return empty, e
			}
			if len(j.Locations) == 0 {
				return empty, ErrInventory
			}
		}
		jobs = append(jobs, j)
	}
	if ctx.Err() != nil {
		return empty, ctx.Err()
	}
	return Inventory{Jobs: jobs, Truncated: truncated}, nil
}

type BrassRingBoard struct{ PartnerID, SiteID string }

var brassRingDigits = regexp.MustCompile(`^[0-9]+$`)

func BrassRingBoardFromURL(raw string) (BrassRingBoard, error) {
	u, e := url.Parse(raw)
	if e != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Hostname() == "" || !strings.Contains(strings.ToLower(u.Path), "/tgnewui/search/") {
		return BrassRingBoard{}, ErrOptions
	}
	q := u.Query()
	p, s := q.Get("partnerid"), q.Get("siteid")
	if p == "" {
		p = q.Get("partnerId")
	}
	if s == "" {
		s = q.Get("siteId")
	}
	if !brassRingDigits.MatchString(p) || !brassRingDigits.MatchString(s) {
		return BrassRingBoard{}, ErrOptions
	}
	return BrassRingBoard{p, s}, nil
}
func brassRingClean(v any) string {
	s, _ := v.(string)
	return strings.Join(strings.Fields(html.UnescapeString(s)), " ")
}
func brassRingQuestions(raw any, name, value string) map[string]any {
	out := map[string]any{}
	a, _ := raw.([]any)
	for _, v := range a {
		m, ok := v.(map[string]any)
		if !ok {
			continue
		}
		k, ok := m[name].(string)
		if ok && k != "" {
			out[cases.Fold().String(k)] = m[value]
		}
	}
	return out
}
func BrassRingLocations(q map[string]any) []string {
	parts := []string{}
	seen := map[string]bool{}
	for _, k := range []string{"formtext8", "formtext9", "formtext10"} {
		s := brassRingClean(q[k])
		fold := cases.Fold().String(s)
		if s != "" && !seen[fold] {
			parts = append(parts, s)
			seen[fold] = true
		}
	}
	if len(parts) > 0 {
		return []string{strings.Join(parts, ", ")}
	}
	if s := brassRingClean(q["location"]); s != "" {
		return []string{s}
	}
	return nil
}
func BrassRingJob(raw any, b BrassRingBoard, normalize SmallDescriptionNormalizer) (Job, bool, error) {
	m, ok := raw.(map[string]any)
	if !ok {
		return Job{}, false, nil
	}
	q := brassRingQuestions(m["Questions"], "QuestionName", "Value")
	id, title, link := brassRingClean(q["reqid"]), brassRingClean(q["jobtitle"]), brassRingClean(m["Link"])
	if !brassRingDigits.MatchString(id) || title == "" || link == "" {
		return Job{}, false, nil
	}
	actual, e := BrassRingBoardFromURL(link)
	u, _ := url.Parse(link)
	if e != nil || actual != b || u.Query().Get("jobid") != id {
		return Job{}, false, nil
	}
	j := Job{URL: link, Title: title, Locations: BrassRingLocations(q), Metadata: map[string]any{"provider": "brassring", "requisition_id": id}}
	if d := brassRingClean(q["department"]); d != "" {
		j.Metadata["department"] = d
	}
	if d, e := time.Parse("02-Jan-2006", brassRingClean(q["lastupdated"])); e == nil {
		j.DatePosted = d.Format("2006-01-02")
	}
	v := q["jobdescription"]
	if !detailTruthy(v) {
		v = q["formtext3"]
	}
	s, _ := v.(string)
	if normalize == nil {
		return Job{}, false, ErrInventory
	}
	description, e := normalize(s)
	if e != nil {
		return Job{}, false, e
	}
	if description != nil {
		j.Description = *description
	}
	return j, true, nil
}
func BrassRingPage(d *Document) (int, []any, error) {
	m, ok := d.Value.(map[string]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	n, ok := darwinboxInteger(m["JobsCount"])
	if !ok {
		return 0, nil, ErrInventory
	}
	if _, ok := m["JobsCount"].(string); ok {
		return 0, nil, ErrInventory
	}
	jobs, _ := m["Jobs"].(map[string]any)
	v := jobs["Job"]
	if v == nil && n == 0 {
		return 0, []any{}, nil
	}
	rows, ok := v.([]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	return n, rows, nil
}

func BrassRingDetailLocation(body, expectedID string) ([]string, error) {
	if len(body) > 16<<20 || !brassRingDigits.MatchString(expectedID) {
		return nil, ErrInventory
	}
	tree, e := xhtml.Parse(strings.NewReader(body))
	if e != nil {
		return nil, ErrInventory
	}
	nodes := inlineSelectedNodes(tree, "#preLoadJSON")
	if len(nodes) == 0 {
		return nil, ErrInventory
	}
	raw, ok := inlineAttribute(nodes[0], "value")
	if !ok || raw == "" {
		return nil, ErrInventory
	}
	d, e := Decode([]byte(raw))
	if e != nil {
		return nil, ErrInventory
	}
	m, ok := d.Value.(map[string]any)
	if !ok || pythonString(m["JobId"]) != expectedID {
		return nil, ErrInventory
	}
	details, _ := m["Jobdetails"].(map[string]any)
	if _, ok := details["JobDetailQuestions"].([]any); !ok {
		return nil, ErrInventory
	}
	q := brassRingQuestions(details["JobDetailQuestions"], "VerityZone", "AnswerValue")
	locations := BrassRingLocations(q)
	if len(locations) == 0 {
		return nil, ErrInventory
	}
	return locations, nil
}
