package documentactions

import (
	"math"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

// The current text locators attach one predicate to the selected element.
// Predicates on ancestors or selector unions need Playwright's selector engine
// and cannot be implemented by filtering the final CSS match's text.
var textInteractionSelector = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_-]*(?:[.#][A-Za-z0-9_-]+)*:has-text\((?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')\)(?::not\(\[disabled\]\))?$`)

func actionKeyAllowed(kind, key string) bool {
	if key == "action" || key == "required" || key == "timeout" {
		return true
	}
	switch kind {
	case "evaluate":
		return key == "script"
	case "remove", "click":
		return key == "selector"
	case "wait":
		return key == "ms"
	case "wait_for":
		return key == "selector" || key == "state"
	case "repeat":
		return key == "selector" || key == "max" || key == "wait_ms"
	case "paginate_collect":
		return key == "next_selector" || key == "max_pages" || key == "wait_ms" || key == "page_size_selector" || key == "page_size"
	}
	return false
}

func interactionSelector(s string, optional bool) bool {
	if s == "" {
		return optional
	}
	if strings.Contains(s, ":has-text(") && !textInteractionSelector.MatchString(s) {
		return false
	}
	// Layout-dependent Playwright selectors and cross-frame locators retain
	// their existing owner until separately qualified in the native engine.
	return utf8.ValidString(s) && len(s) <= 4096 && !strings.ContainsRune(s, 0) && !strings.Contains(s, ":visible") && !strings.Contains(s, ">>")
}

func interactionWait(ms float64) bool {
	return !math.IsNaN(ms) && !math.IsInf(ms, 0) && ms >= 0 && ms <= 120000
}

func validInteraction(a Action) bool {
	if a.Script != "" || a.Milliseconds != 0 {
		return false
	}
	switch a.Kind {
	case "click":
		return interactionSelector(a.Selector, false) && a.State == "" && a.Maximum == 0 && a.WaitMS == 0 && a.NextSelector == "" && a.MaxPages == 0 && a.PageSizeSelector == "" && a.PageSize == ""
	case "wait_for":
		return interactionSelector(a.Selector, false) && (a.State == "attached" || a.State == "detached") && a.Maximum == 0 && a.WaitMS == 0 && a.NextSelector == "" && a.MaxPages == 0 && a.PageSizeSelector == "" && a.PageSize == ""
	case "repeat":
		return interactionSelector(a.Selector, false) && a.State == "" && a.Maximum >= 1 && a.Maximum <= 1000 && interactionWait(a.WaitMS) && a.NextSelector == "" && a.MaxPages == 0 && a.PageSizeSelector == "" && a.PageSize == ""
	case "paginate_collect":
		return a.Required && a.Selector == "" && a.State == "" && a.Maximum == 0 && interactionSelector(a.NextSelector, false) && a.MaxPages >= 1 && a.MaxPages <= 1000 && interactionWait(a.WaitMS) && interactionSelector(a.PageSizeSelector, true) && utf8.ValidString(a.PageSize) && len(a.PageSize) <= 64 && !strings.ContainsRune(a.PageSize, 0)
	}
	return false
}

func interactionNumber(m map[string]any, key string, fallback float64) (float64, error) {
	v, exists := m[key]
	if !exists {
		return fallback, nil
	}
	n, ok := v.(float64)
	if !ok || math.IsNaN(n) || math.IsInf(n, 0) {
		return 0, ErrActions
	}
	return n, nil
}

func interactionCount(m map[string]any, key string, fallback uint64) (uint64, error) {
	n, err := interactionNumber(m, key, float64(fallback))
	if err != nil || n < 1 || n > 1000 || n != math.Trunc(n) {
		return 0, ErrActions
	}
	return uint64(n), nil
}

func parseInteraction(a *Action, m map[string]any) error {
	var ok bool
	if a.Kind != "paginate_collect" {
		a.Selector, ok = m["selector"].(string)
		if !ok {
			return ErrActions
		}
	}
	switch a.Kind {
	case "wait_for":
		a.State = "visible"
		if value, exists := m["state"]; exists {
			a.State, ok = value.(string)
			if !ok {
				return ErrActions
			}
		}
	case "repeat":
		var err error
		a.Maximum, err = interactionCount(m, "max", 50)
		if err != nil {
			return err
		}
		a.WaitMS, err = interactionNumber(m, "wait_ms", 2000)
		if err != nil {
			return err
		}
	case "paginate_collect":
		// The original collector always rejects incomplete pagination, even
		// when the configuration spells required:false.
		a.Required = true
		a.NextSelector = "li.next:not(.next_disabled) a"
		if value, exists := m["next_selector"]; exists {
			a.NextSelector, ok = value.(string)
			if !ok {
				return ErrActions
			}
		}
		var err error
		a.MaxPages, err = interactionCount(m, "max_pages", 50)
		if err != nil {
			return err
		}
		a.WaitMS, err = interactionNumber(m, "wait_ms", 5000)
		if err != nil {
			return err
		}
		if value, exists := m["page_size_selector"]; exists {
			a.PageSizeSelector, ok = value.(string)
			if !ok {
				return ErrActions
			}
		}
		if value, exists := m["page_size"]; exists {
			switch value := value.(type) {
			case string:
				a.PageSize = value
			case float64:
				if value < 0 || value > 100000 || value != math.Trunc(value) {
					return ErrActions
				}
				// Numeric zero is false in the original page-size guard; the
				// nonempty string "0" still executes its change event.
				if value != 0 {
					a.PageSize = strconv.FormatUint(uint64(value), 10)
				}
			default:
				return ErrActions
			}
		}
	}
	if !validInteraction(*a) {
		return ErrActions
	}
	return nil
}
