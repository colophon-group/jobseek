package documentactions

import (
	"regexp"
	"unicode/utf8"
)

// InlineCards binds provider control identities to complete expanded fragments.
// Identities are returned separately from page-controlled HTML.
type InlineCards struct {
	ControlSelector   string `json:"control_selector"`
	ContentIndex      uint64 `json:"content_index"`
	ContentSelector   string `json:"content_selector"`
	IdentitySelector  string `json:"identity_selector"`
	IdentityAttribute string `json:"identity_attribute"`
	IdentityPattern   string `json:"identity_pattern"`
	Maximum           uint64 `json:"maximum"`
	TimeoutMS         uint64 `json:"timeout_ms"`
}

var inlineCardAttribute = regexp.MustCompile(`^[A-Za-z_:][A-Za-z0-9:._-]*$`)

func (c InlineCards) Valid() bool {
	if !interactionSelector(c.ControlSelector, false) || !interactionSelector(c.ContentSelector, false) || !interactionSelector(c.IdentitySelector, false) || !inlineCardAttribute.MatchString(c.IdentityAttribute) || c.ContentIndex > 100 || c.Maximum < 1 || c.Maximum > 500 || c.TimeoutMS == 0 || c.TimeoutMS > 300000 || !utf8.ValidString(c.IdentityPattern) || len(c.IdentityPattern) == 0 || len(c.IdentityPattern) > 2048 {
		return false
	}
	pattern, err := regexp.Compile(`^(?:` + c.IdentityPattern + `)$`)
	return err == nil && pattern.NumSubexp() == 1
}

func (c InlineCards) Identity(raw string) (string, bool) {
	if !c.Valid() || len(raw) > 4096 {
		return "", false
	}
	pattern, _ := regexp.Compile(`^(?:` + c.IdentityPattern + `)$`)
	values := pattern.FindStringSubmatch(raw)
	if len(values) != 2 || values[1] == "" || len([]rune(values[1])) > 512 {
		return "", false
	}
	return values[1], true
}
