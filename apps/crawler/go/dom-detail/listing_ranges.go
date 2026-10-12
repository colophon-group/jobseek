package dom

import (
	"sort"
	"strings"

	"github.com/dlclark/regexp2/v2"
	"golang.org/x/net/html"
)

// ListingAdvertisedRanges retains the publisher's contiguous start-end labels.
type ListingAdvertisedRanges struct {
	Selector string
	Pattern  *regexp2.Regexp
}

func listingAdvertisedRangesOptions(raw any) (*ListingAdvertisedRanges, error) {
	if raw == nil {
		return nil, nil
	}
	m, err := object(raw)
	if err != nil || len(m) != 2 || m["selector"] == nil || m["pattern"] == nil {
		return nil, ErrListingProof
	}
	r := &ListingAdvertisedRanges{}
	r.Selector, err = proofSelector(m["selector"], true)
	if err != nil {
		return nil, err
	}
	r.Pattern, err = proofPattern(m["pattern"])
	if err != nil || len(r.Pattern.GetGroupNumbers()) != 3 {
		return nil, ErrListingProof
	}
	return r, nil
}

func ListingRangeTotal(source string, r *ListingAdvertisedRanges, initialCount int) (*int, error) {
	if r == nil || initialCount < 1 {
		return nil, ErrListingProof
	}
	tree, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
	if err != nil {
		return nil, err
	}
	seen := map[[2]int]bool{}
	for _, node := range proofNodes(tree, r.Selector) {
		match, err := proofFullMatch(r.Pattern, strings.Join(strings.Fields(richRowsText(node, " ")), " "))
		if err != nil {
			return nil, err
		}
		if match == nil {
			continue
		}
		start, a := proofDecimal(match.Groups()[1].String())
		end, b := proofDecimal(match.Groups()[2].String())
		if a != nil || b != nil || start < 1 || end < start || end > 50_000 {
			return nil, ErrListingProof
		}
		seen[[2]int{start, end}] = true
	}
	ranges := make([][2]int, 0, len(seen))
	for pair := range seen {
		ranges = append(ranges, pair)
	}
	sort.Slice(ranges, func(i, j int) bool {
		if ranges[i][0] != ranges[j][0] {
			return ranges[i][0] < ranges[j][0]
		}
		return ranges[i][1] < ranges[j][1]
	})
	if len(ranges) == 0 || ranges[0][0] != 1 || ranges[0][1] != initialCount {
		return nil, ErrListingProof
	}
	total := 0
	for _, pair := range ranges {
		if pair[0] != total+1 {
			return nil, ErrListingProof
		}
		total = pair[1]
	}
	return &total, nil
}
