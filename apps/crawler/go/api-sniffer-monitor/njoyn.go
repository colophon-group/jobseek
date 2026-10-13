package apisniffer

import (
	"context"
	"errors"
	"net/url"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

const NjoynMaxJobs = 50000
const NjoynMaxPages = 200

var ErrNjoynSnapshotChanged = errors.New("Njoyn inventory changed")
var njoynTotalPattern = regexp.MustCompile(`(?i)\bSearch\s+Results\s*\(([0-9,\s]+)\)`)
var njoynPagesPattern = regexp.MustCompile(`(?i)\bPage\s+([0-9,\s]+)\s+of\s+([0-9,\s]+)\b`)
var njoynPositivePattern = regexp.MustCompile(`^[1-9][0-9]*$`)
var njoynNondigit = regexp.MustCompile(`[^0-9]`)

type NjoynBoard struct {
	URL, Host, Tenant string
}

func njoynQuery(raw string) (map[string][]string, error) {
	// Match parse_qs' insertion order before the original case-folded mapping.
	// Repeated keys retain all values so single-value identity checks refuse them.
	values := map[string][]string{}
	var keys []string
	for _, pair := range strings.Split(raw, "&") {
		if pair == "" {
			continue
		}
		key, value, _ := strings.Cut(pair, "=")
		key, err := url.QueryUnescape(key)
		if err != nil {
			return nil, ErrOptions
		}
		value, err = url.QueryUnescape(value)
		if err != nil {
			return nil, ErrOptions
		}
		if _, ok := values[key]; !ok {
			keys = append(keys, key)
		}
		values[key] = append(values[key], value)
	}
	out := map[string][]string{}
	for _, key := range keys {
		out[strings.ToLower(key)] = values[key]
	}
	return out, nil
}

func njoynSingle(query map[string][]string, key string) string {
	values := query[key]
	if len(values) != 1 {
		return ""
	}
	return inlineTrim(values[0])
}

func njoynIdentity(raw string) (NjoynBoard, map[string][]string, error) {
	u, err := url.Parse(raw)
	if err != nil || !strings.EqualFold(u.Scheme, "https") || u.User != nil || u.Port() != "" && u.Port() != "443" || !strings.HasSuffix(strings.ToLower(u.Hostname()), ".njoyn.com") || !strings.Contains(strings.ToLower(u.Path), "/xweb/") {
		return NjoynBoard{}, nil, ErrOptions
	}
	query, err := njoynQuery(u.RawQuery)
	tenant := njoynSingle(query, "clid")
	if err != nil || tenant == "" {
		return NjoynBoard{}, nil, ErrOptions
	}
	return NjoynBoard{raw, strings.ToLower(u.Hostname()), strings.ToLower(tenant)}, query, nil
}

func NjoynBoardFromURL(raw string) (NjoynBoard, error) {
	board, query, err := njoynIdentity(raw)
	if err != nil || !strings.EqualFold(njoynSingle(query, "page"), "joblisting") {
		return NjoynBoard{}, ErrOptions
	}
	return board, nil
}

func NjoynDetailMatches(board NjoynBoard, raw string) bool {
	posting, query, err := njoynIdentity(raw)
	return err == nil && posting.Host == board.Host && posting.Tenant == board.Tenant && strings.EqualFold(njoynSingle(query, "page"), "jobdetails") && njoynSingle(query, "jobid") != "" && njoynSingle(query, "brid") != ""
}

type NjoynSnapshot struct {
	URLs                         []string
	Total, PageNumber, PageCount int
}

// The affine browser reads exactly the original links/body.innerText/hidden pn
// state. Missing advertised totals cannot become a successful empty inventory.
func NjoynSnapshotFromState(board NjoynBoard, text, hiddenPage string, links []string) (NjoynSnapshot, error) {
	var out NjoynSnapshot
	if !njoynPositivePattern.MatchString(hiddenPage) {
		return out, ErrNjoynSnapshotChanged
	}
	page, err := strconv.Atoi(hiddenPage)
	if err != nil {
		return out, ErrNjoynSnapshotChanged
	}
	totalMatch := njoynTotalPattern.FindStringSubmatch(text)
	pagesMatch := njoynPagesPattern.FindStringSubmatch(text)
	if len(totalMatch) != 2 || len(pagesMatch) != 3 {
		return out, ErrInventory
	}
	parse := func(raw string) (int, error) { return strconv.Atoi(njoynNondigit.ReplaceAllString(raw, "")) }
	total, e1 := parse(totalMatch[1])
	visible, e2 := parse(pagesMatch[1])
	count, e3 := parse(pagesMatch[2])
	if e1 != nil || e2 != nil || e3 != nil || total > NjoynMaxJobs {
		return out, ErrInventory
	}
	if page != visible || count < 1 || page > count {
		return out, ErrNjoynSnapshotChanged
	}
	seen := map[string]bool{}
	for _, link := range links {
		if NjoynDetailMatches(board, link) {
			seen[link] = true
		}
	}
	urls := make([]string, 0, len(seen))
	for raw := range seen {
		urls = append(urls, raw)
	}
	slices.Sort(urls)
	return NjoynSnapshot{urls, total, page, count}, nil
}

func NjoynValidatePageShape(page NjoynSnapshot, pageSize, maxPages int) error {
	if page.PageCount > maxPages {
		return ErrInventory
	}
	if page.Total == 0 {
		if page.PageCount != 1 || len(page.URLs) != 0 {
			return ErrNjoynSnapshotChanged
		}
		return nil
	}
	if pageSize < 1 {
		return ErrInventory
	}
	if page.PageCount != (page.Total+pageSize-1)/pageSize {
		return ErrNjoynSnapshotChanged
	}
	expected := pageSize
	if page.PageNumber == page.PageCount {
		expected = page.Total - pageSize*(page.PageCount-1)
	}
	if len(page.URLs) != expected {
		return ErrNjoynSnapshotChanged
	}
	return nil
}

type NjoynPass struct {
	URLs   []string
	Totals []int
	Pages  int
}

func njoynAllowance(total int) int { return min(25, max(1, (total+199)/200)) }

func NjoynReconcile(first, second NjoynPass) ([]string, error) {
	totals := append(slices.Clone(first.Totals), second.Totals...)
	if len(totals) == 0 {
		return nil, ErrOptions
	}
	low, high := slices.Min(totals), slices.Max(totals)
	allowance := njoynAllowance(high)
	if high-low > allowance {
		return nil, ErrNjoynSnapshotChanged
	}
	firstURLs, secondURLs, union := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, raw := range first.URLs {
		firstURLs[raw], union[raw] = true, true
	}
	for _, raw := range second.URLs {
		secondURLs[raw], union[raw] = true, true
	}
	difference := 0
	for raw := range union {
		if firstURLs[raw] != secondURLs[raw] {
			difference++
		}
	}
	if difference > allowance*2 || len(union) > NjoynMaxJobs || len(union)-high > allowance || high-len(union) > allowance {
		return nil, ErrNjoynSnapshotChanged
	}
	out := make([]string, 0, len(union))
	for raw := range union {
		out = append(out, raw)
	}
	slices.Sort(out)
	return out, nil
}

type NjoynControls struct {
	First func(context.Context) (NjoynSnapshot, error)
	Page  func(context.Context, int) (NjoynSnapshot, error)
	Wait  func(context.Context, time.Duration) error
}

func njoynWait(ctx context.Context, wait func(context.Context, time.Duration) error, delay time.Duration) error {
	if wait != nil {
		return wait(ctx, delay)
	}
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func njoynListingPass(ctx context.Context, controls NjoynControls, maxPages int) (NjoynPass, error) {
	first, err := controls.First(ctx)
	if err != nil {
		return NjoynPass{}, err
	}
	if first.PageNumber != 1 {
		return NjoynPass{}, ErrNjoynSnapshotChanged
	}
	size := len(first.URLs)
	if err := NjoynValidatePageShape(first, size, maxPages); err != nil {
		return NjoynPass{}, err
	}
	urls := map[string]bool{}
	for _, raw := range first.URLs {
		urls[raw] = true
	}
	totals, current := []int{first.Total}, first
	for target := 2; target <= current.PageCount; target++ {
		var candidate NjoynSnapshot
		accepted := false
		for attempt := 1; attempt <= 3; attempt++ {
			candidate, err = controls.Page(ctx, target)
			if err != nil {
				return NjoynPass{}, err
			}
			fresh := 0
			for _, raw := range candidate.URLs {
				if !urls[raw] {
					fresh++
				}
			}
			if candidate.PageNumber == target && !slices.Equal(candidate.URLs, current.URLs) && fresh > 0 {
				accepted = true
				break
			}
			if attempt < 3 {
				if err := njoynWait(ctx, controls.Wait, time.Duration(attempt)*time.Second); err != nil {
					return NjoynPass{}, err
				}
			}
		}
		if !accepted {
			return NjoynPass{}, ErrNjoynSnapshotChanged
		}
		if err := NjoynValidatePageShape(candidate, size, maxPages); err != nil {
			return NjoynPass{}, err
		}
		for _, raw := range candidate.URLs {
			urls[raw] = true
		}
		totals = append(totals, candidate.Total)
		current = candidate
	}
	low, high := slices.Min(totals), slices.Max(totals)
	allowance := njoynAllowance(high)
	if high-low > allowance || len(urls)-high > allowance || high-len(urls) > allowance {
		return NjoynPass{}, ErrNjoynSnapshotChanged
	}
	out := make([]string, 0, len(urls))
	for raw := range urls {
		out = append(out, raw)
	}
	slices.Sort(out)
	return NjoynPass{out, totals, current.PageNumber}, nil
}

// One inventory consists of two structurally complete passes. Only bounded
// snapshot changes retry the whole pair; transport/publisher errors propagate.
func DiscoverNjoyn(ctx context.Context, controls NjoynControls, maxPages, snapshotAttempts int) (Inventory, error) {
	result := Inventory{Jobs: []Job{}, URLOnly: true}
	maxPages, snapshotAttempts = min(NjoynMaxPages, maxPages), min(3, max(1, snapshotAttempts))
	if controls.First == nil || controls.Page == nil || maxPages < 1 {
		return result, ErrOptions
	}
	for attempt := 1; attempt <= snapshotAttempts; attempt++ {
		first, err := njoynListingPass(ctx, controls, maxPages)
		var second NjoynPass
		if err == nil {
			second, err = njoynListingPass(ctx, controls, maxPages)
		}
		var urls []string
		if err == nil {
			urls, err = NjoynReconcile(first, second)
		}
		if err == nil {
			for _, raw := range urls {
				result.Jobs = append(result.Jobs, Job{URL: raw})
			}
			return result, nil
		}
		if !errors.Is(err, ErrNjoynSnapshotChanged) {
			return result, err
		}
		if attempt < snapshotAttempts {
			if err := njoynWait(ctx, controls.Wait, time.Duration(attempt)*2*time.Second); err != nil {
				return result, err
			}
		}
	}
	return result, ErrNjoynSnapshotChanged
}
