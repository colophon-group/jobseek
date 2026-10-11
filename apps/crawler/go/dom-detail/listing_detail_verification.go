package dom

import (
	"errors"
	"golang.org/x/net/html"
	"strings"
)

type InactiveDetailState struct{ Selector, ExactText string }

var ErrDetailVerification = errors.New("DOM detail verification failed")

func listingDetailVerificationOptions(options Object, c *ListingConfig) error {
	var err error
	c.ExcludeDetailSelector, err = proofSelector(options["exclude_detail_selector"], false)
	if err != nil {
		return err
	}
	if raw := options["inactive_detail_states"]; raw != nil {
		states, ok := raw.([]any)
		if !ok || len(states) < 1 || len(states) > 4 {
			return ErrDetailVerification
		}
		for _, raw := range states {
			state, ok := raw.(map[string]any)
			if !ok || len(state) != 2 {
				return ErrDetailVerification
			}
			selector, err := proofSelector(state["selector"], true)
			exact, ok := state["exact_text"].(string)
			if err != nil || !ok || trim(exact) == "" || len([]rune(exact)) > 512 || strings.ContainsRune(exact, 0) {
				return ErrDetailVerification
			}
			c.InactiveDetailStates = append(c.InactiveDetailStates, InactiveDetailState{selector, strings.Join(strings.FieldsFunc(exact, pySpace), " ")})
		}
	}
	if len(c.InactiveDetailStates) > 0 && (truth(options["render"]) || c.Pagination != nil || c.Selector == "" && c.OnclickSelector == "") {
		return ErrDetailVerification
	}
	if c.HasDetailFilters() && (c.RichRows != nil || c.ScriptLinks != nil || c.IncludeBoardURL || c.FetchURL != "") {
		return ErrDetailVerification
	}
	return nil
}
func (c ListingConfig) HasDetailFilters() bool {
	return c.ExcludeDetailSelector != "" || len(c.InactiveDetailStates) > 0
}
func DetailSelectorKeeps(source string, c ListingConfig, inactive bool) (bool, error) {
	tree, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return false, ErrDetailVerification
	}
	if !inactive {
		return len(proofNodes(tree, c.ExcludeDetailSelector)) == 0, nil
	}
	observed := false
	for _, state := range c.InactiveDetailStates {
		for _, node := range proofNodes(tree, state.Selector) {
			text := strings.Join(strings.FieldsFunc(richRowsText(node, " "), pySpace), " ")
			if text == state.ExactText {
				return false, nil
			}
			observed = true
		}
	}
	if observed {
		return false, ErrDetailVerification
	}
	return true, nil
}
