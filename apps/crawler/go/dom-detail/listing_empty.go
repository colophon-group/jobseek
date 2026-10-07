package dom

import (
	"errors"
	"github.com/andybalholm/cascadia"
	"golang.org/x/net/html"
	"golang.org/x/text/cases"
	"strings"
)

var ErrListingEmpty = errors.New("DOM inventory lacks configured explicit empty evidence")

func listingEmptyOptions(config Object, c *ListingConfig) error {
	for key, target := range map[string]*string{"empty_selector": &c.EmptySelector, "empty_text": &c.EmptyText} {
		v := config[key]
		if v == nil {
			continue
		}
		s, ok := v.(string)
		if !ok || strings.TrimSpace(s) == "" || len([]rune(s)) > 256 || strings.ContainsRune(s, 0) {
			return ErrListingEmpty
		}
		*target = strings.TrimSpace(s)
	}
	if c.EmptySelector == "" {
		if c.EmptyText != "" {
			return ErrListingEmpty
		}
		return nil
	}
	if c.Selector == "" || c.Pagination != nil || c.Encoding != "" {
		return ErrListingEmpty
	}
	_, err := cascadia.Parse(c.EmptySelector)
	return err
}

// Python's legacy proof uses the first selected element and normalized,
// case-insensitive substring text. A later unrelated marker cannot prove zero.
func ValidateListingEmpty(source string, c ListingConfig, jobCount int) error {
	if c.EmptySelector == "" || jobCount > 0 {
		return nil
	}
	tree, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
	if err != nil {
		return err
	}
	selector, err := cascadia.Parse(c.EmptySelector)
	if err != nil {
		return err
	}
	marker := cascadia.Query(tree, selector)
	if marker == nil {
		return ErrListingEmpty
	}
	if c.EmptyText == "" {
		return nil
	}
	actual := cases.Fold().String(strings.Join(strings.Fields(richRowsText(marker, " ")), " "))
	expected := cases.Fold().String(strings.Join(strings.Fields(c.EmptyText), " "))
	if !strings.Contains(actual, expected) {
		return ErrListingEmpty
	}
	return nil
}
