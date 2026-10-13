package apisniffer

import (
	"context"
	"errors"
	"html"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/andybalholm/cascadia"
	htmlnode "golang.org/x/net/html"
	"golang.org/x/text/cases"
)

const BrassRingMaxJobs = 50000

var ErrBrassRingSnapshotChanged = errors.New("BrassRing inventory changed")
var brassRingDigits = regexp.MustCompile(`^[0-9]+$`)

type BrassRingBoard struct {
	URL, PartnerID, SiteID string
}

func BrassRingBoardFromURL(raw string) (BrassRingBoard, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" || u.Scheme != "https" && u.Scheme != "http" || !strings.Contains(strings.ToLower(u.Path), "/tgnewui/search/") {
		return BrassRingBoard{}, ErrOptions
	}
	q := u.Query()
	partner, site := q.Get("partnerid"), q.Get("siteid")
	if partner == "" {
		partner = q.Get("partnerId")
	}
	if site == "" {
		site = q.Get("siteId")
	}
	if !brassRingDigits.MatchString(partner) || !brassRingDigits.MatchString(site) {
		return BrassRingBoard{}, ErrOptions
	}
	return BrassRingBoard{raw, partner, site}, nil
}

func brassRingClean(raw any) string {
	s, _ := raw.(string)
	return inlineNormalized(html.UnescapeString(s))
}

func brassRingQuestions(raw any, name, value string) map[string]any {
	questions := map[string]any{}
	rows, _ := raw.([]any)
	for _, row := range rows {
		q, ok := row.(map[string]any)
		if !ok {
			continue
		}
		key, _ := q[name].(string)
		if key != "" {
			questions[cases.Fold().String(key)] = q[value]
		}
	}
	return questions
}

func brassRingLocation(questions map[string]any) []string {
	var parts []string
	seen := map[string]bool{}
	for _, key := range []string{"formtext8", "formtext9", "formtext10"} {
		part := brassRingClean(questions[key])
		folded := cases.Fold().String(part)
		if part != "" && !seen[folded] {
			seen[folded] = true
			parts = append(parts, part)
		}
	}
	if len(parts) > 0 {
		return []string{strings.Join(parts, ", ")}
	}
	if location := brassRingClean(questions["location"]); location != "" {
		return []string{location}
	}
	return nil
}

func BrassRingJob(raw any, board BrassRingBoard, normalize SmallDescriptionNormalizer) (Job, error) {
	row, ok := raw.(map[string]any)
	if !ok {
		return Job{}, ErrBrassRingSnapshotChanged
	}
	questions := brassRingQuestions(row["Questions"], "QuestionName", "Value")
	id, title, link := brassRingClean(questions["reqid"]), brassRingClean(questions["jobtitle"]), brassRingClean(row["Link"])
	linked, err := BrassRingBoardFromURL(link)
	u, _ := url.Parse(link)
	if !brassRingDigits.MatchString(id) || title == "" || err != nil || linked.PartnerID != board.PartnerID || linked.SiteID != board.SiteID || u.Query().Get("jobid") != id {
		return Job{}, ErrBrassRingSnapshotChanged
	}
	rawDescription := questions["jobdescription"]
	if !detailTruthy(rawDescription) {
		rawDescription = questions["formtext3"]
	}
	description, err := portalNormalize(rawDescription, normalize)
	if err != nil {
		return Job{}, err
	}
	metadata := map[string]any{"provider": "brassring", "requisition_id": id}
	if department := brassRingClean(questions["department"]); department != "" {
		metadata["department"] = department
	}
	var posted any
	if date, err := time.Parse("2-Jan-2006", brassRingClean(questions["lastupdated"])); err == nil {
		posted = date.Format("2006-01-02")
	}
	return Job{URL: link, Title: title, Description: description, Locations: brassRingLocation(questions), DatePosted: posted, Metadata: metadata}, nil
}

func BrassRingDetailLocation(body, expectedID string) ([]string, error) {
	root, err := htmlnode.Parse(strings.NewReader(body))
	if err != nil {
		return nil, ErrInventory
	}
	preload := cascadia.Query(root, cascadia.MustCompile("#preLoadJSON"))
	var payload string
	if preload != nil {
		for _, attribute := range preload.Attr {
			if attribute.Key == "value" {
				payload = attribute.Val
			}
		}
	}
	d, err := Decode([]byte(payload))
	if err != nil {
		return nil, ErrInventory
	}
	value, ok := d.Value.(map[string]any)
	if !ok || pythonString(value["JobId"]) != expectedID {
		return nil, ErrInventory
	}
	details, _ := value["Jobdetails"].(map[string]any)
	if _, ok := details["JobDetailQuestions"].([]any); !ok {
		return nil, ErrInventory
	}
	location := brassRingLocation(brassRingQuestions(details["JobDetailQuestions"], "VerityZone", "AnswerValue"))
	if len(location) == 0 {
		return nil, ErrInventory
	}
	return location, nil
}

func BrassRingPage(document *Document) (int, []any, error) {
	if document == nil {
		return 0, nil, ErrInventory
	}
	value, ok := document.Value.(map[string]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	total, ok := integer(value["JobsCount"])
	if !ok || total < 0 {
		return 0, nil, ErrInventory
	}
	jobs, _ := value["Jobs"].(map[string]any)
	if jobs["Job"] == nil && total == 0 {
		return 0, []any{}, nil
	}
	rows, ok := jobs["Job"].([]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	return total, rows, nil
}

// Each callback is a scoped provider control, rather than an arbitrary URL or
// selector. Next returns only after its visible page index is verified. The
// outer browser runner owns the original two fresh-session snapshot attempts.
type BrassRingControls struct {
	Search func(context.Context) (*Document, error)
	Sort   func(context.Context) (*Document, error)
	Next   func(context.Context, int) (*Document, error)
}

func DiscoverBrassRing(ctx context.Context, board BrassRingBoard, controls BrassRingControls, normalize SmallDescriptionNormalizer, hydrate func(context.Context, string, string) ([]string, error)) (Inventory, error) {
	result := Inventory{Jobs: []Job{}}
	if controls.Search == nil || controls.Sort == nil || controls.Next == nil || normalize == nil {
		return result, ErrOptions
	}
	first, err := controls.Search(ctx)
	if err != nil {
		return result, err
	}
	total, rows, err := BrassRingPage(first)
	if err != nil {
		return result, err
	}
	pageCount := func(total, size int) int {
		if size == 0 {
			size = 50
		}
		return (min(total, BrassRingMaxJobs) + size - 1) / size
	}
	if total > 0 && len(rows) == 0 {
		return result, ErrBrassRingSnapshotChanged
	}
	pages := pageCount(total, len(rows))
	if pages > 1 {
		sorted, err := controls.Sort(ctx)
		if err != nil {
			return result, err
		}
		sortedTotal, sortedRows, err := BrassRingPage(sorted)
		if err != nil {
			return result, err
		}
		if total > 0 && sortedTotal == 0 || sortedTotal > 0 && len(sortedRows) == 0 {
			return result, ErrBrassRingSnapshotChanged
		}
		total, rows = sortedTotal, sortedRows
		pages = pageCount(total, len(rows))
	}
	for page := 2; page <= pages; page++ {
		d, err := controls.Next(ctx, page)
		if err != nil {
			return result, err
		}
		count, following, err := BrassRingPage(d)
		if err != nil {
			return result, err
		}
		if count != total {
			return result, ErrBrassRingSnapshotChanged
		}
		rows = append(rows, following...)
	}
	expected := min(total, BrassRingMaxJobs)
	if len(rows) < expected || total <= BrassRingMaxJobs && len(rows) != expected {
		return result, ErrBrassRingSnapshotChanged
	}
	seen := map[string]bool{}
	for _, raw := range rows[:expected] {
		job, err := BrassRingJob(raw, board, normalize)
		if err != nil {
			return Inventory{Jobs: []Job{}}, err
		}
		id := job.Metadata["requisition_id"].(string)
		if seen[id] {
			return Inventory{Jobs: []Job{}}, ErrBrassRingSnapshotChanged
		}
		seen[id] = true
		result.Jobs = append(result.Jobs, job)
	}
	if err := brassRingHydrate(ctx, result.Jobs, hydrate); err != nil {
		return Inventory{Jobs: []Job{}}, err
	}
	result.Truncated = total > BrassRingMaxJobs
	return result, nil
}

// Validate the entire snapshot before any optional detail request, then keep
// the original eight-request hydration bound. A failed detail aborts inventory.
func brassRingHydrate(ctx context.Context, jobs []Job, hydrate func(context.Context, string, string) ([]string, error)) error {
	var missing []int
	for i := range jobs {
		if len(jobs[i].Locations) == 0 {
			missing = append(missing, i)
		}
	}
	if len(missing) == 0 {
		return nil
	}
	if hydrate == nil {
		return ErrInventory
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	work := make(chan int)
	failures := make(chan error, 1)
	var workers sync.WaitGroup
	for range min(8, len(missing)) {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for i := range work {
				if ctx.Err() != nil {
					return
				}
				location, err := hydrate(ctx, jobs[i].URL, jobs[i].Metadata["requisition_id"].(string))
				if err == nil && len(location) == 0 {
					err = ErrInventory
				}
				if err != nil {
					select {
					case failures <- err:
					default:
					}
					cancel()
					return
				}
				jobs[i].Locations = location
			}
		}()
	}
feed:
	for _, i := range missing {
		select {
		case work <- i:
		case <-ctx.Done():
			break feed
		}
	}
	close(work)
	workers.Wait()
	select {
	case err := <-failures:
		return err
	default:
		return ctx.Err()
	}
}
