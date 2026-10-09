package queue

import (
	"encoding/json"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"github.com/dlclark/regexp2/v2"
	"math/big"
	"strconv"
	"strings"
)

// FeedCollision authenticates aliases before choosing canonical content.
// It grants no network, queue or database authority.
type FeedCollision struct {
	find, canonical, sourceIdentity         *regexp2.Regexp
	preferredSource, preferredMetadata      []*regexp2.Regexp
	identityKey, preferenceKey, replacement string
	bufferLimit                             int
}
type FeedCollisionRank struct {
	Metadata, Source int
	URL              string
}

func (a FeedCollisionRank) Before(b FeedCollisionRank) bool {
	if a.Metadata != b.Metadata {
		return a.Metadata < b.Metadata
	}
	if a.Source != b.Source {
		return a.Source < b.Source
	}
	return a.URL < b.URL
}
func (r *FeedURLRules) HasCollision() bool { return r != nil && r.collision != nil }
func (r *FeedURLRules) CollisionBufferLimit() int {
	if !r.HasCollision() {
		return 0
	}
	return r.collision.bufferLimit
}

func parseFeedCollision(raw string) (*FeedCollision, error) {
	keys := map[string]bool{"find": true, "replace": true, "collision_policy": true, "collision_preferred_source_patterns": true, "collision_preferred_metadata_key": true, "collision_preferred_metadata_patterns": true, "collision_canonical_identity_regex": true, "collision_identity_metadata_key": true, "collision_source_identity_regex": true, "collision_stream_buffer_limit": true}
	md, err := profileMetadataFields(raw, keys)
	if err != nil {
		return nil, err
	}
	text := func(k string, max int, required bool) (string, error) {
		v, present := md[k]
		if !present || string(v) == "null" {
			if required {
				return "", ErrUnsupportedProfile
			}
			return "", nil
		}
		nonempty := required || k == "collision_identity_metadata_key" || k == "collision_source_identity_regex" || k == "collision_preferred_metadata_key"
		var s string
		if json.Unmarshal(v, &s) != nil || len(s) > max || nonempty && s == "" {
			return "", ErrUnsupportedProfile
		}
		return s, nil
	}
	policy, err := text("collision_policy", 32, true)
	if err != nil || policy != "prefer_source_pattern" {
		return nil, ErrUnsupportedProfile
	}
	c := &FeedCollision{}
	var find string
	if find, err = text("find", 2048, true); err != nil {
		return nil, err
	}
	if c.replacement, err = text("replace", 4096, false); err != nil {
		return nil, err
	}
	if c.find, err = dom.CompileURLPattern(find); err != nil {
		return nil, ErrUnsupportedProfile
	}
	pattern := func(k string, required bool) (*regexp2.Regexp, error) {
		s, e := text(k, 2048, required)
		if e != nil {
			return nil, e
		}
		if s == "" {
			return nil, nil
		}
		re, e := dom.CompileURLPattern(`\A(?:` + s + `)\Z`)
		if e != nil || len(re.GetGroupNumbers()) != 2 {
			return nil, ErrUnsupportedProfile
		}
		return re, nil
	}
	if c.canonical, err = pattern("collision_canonical_identity_regex", true); err != nil {
		return nil, err
	}
	if c.sourceIdentity, err = pattern("collision_source_identity_regex", false); err != nil {
		return nil, err
	}
	if c.identityKey, err = text("collision_identity_metadata_key", 128, false); err != nil {
		return nil, err
	}
	if (c.identityKey == "") == (c.sourceIdentity == nil) {
		return nil, ErrUnsupportedProfile
	}
	if c.preferenceKey, err = text("collision_preferred_metadata_key", 128, false); err != nil {
		return nil, err
	}
	patterns := func(k string, required bool) ([]*regexp2.Regexp, error) {
		v, ok := md[k]
		if !ok || string(v) == "null" {
			if required {
				return nil, ErrUnsupportedProfile
			}
			return nil, nil
		}
		var names []string
		if json.Unmarshal(v, &names) != nil || len(names) == 0 || len(names) > 32 {
			return nil, ErrUnsupportedProfile
		}
		out := make([]*regexp2.Regexp, 0, len(names))
		for _, s := range names {
			if s == "" || len(s) > 2048 {
				return nil, ErrUnsupportedProfile
			}
			re, e := dom.CompileURLPattern(s)
			if e != nil {
				return nil, ErrUnsupportedProfile
			}
			out = append(out, re)
		}
		return out, nil
	}
	if c.preferredSource, err = patterns("collision_preferred_source_patterns", true); err != nil {
		return nil, err
	}
	if c.preferredMetadata, err = patterns("collision_preferred_metadata_patterns", false); err != nil {
		return nil, err
	}
	if (c.preferenceKey == "") != (len(c.preferredMetadata) == 0) {
		return nil, ErrUnsupportedProfile
	}
	if json.Unmarshal(md["collision_stream_buffer_limit"], &c.bufferLimit) != nil || c.bufferLimit < 1 || c.bufferLimit > 50000 {
		return nil, ErrUnsupportedProfile
	}
	if strings.Count(c.replacement, "{identity}") > 1 || strings.Contains(c.replacement, "{identity}") && c.identityKey == "" {
		return nil, ErrUnsupportedProfile
	}
	if _, err = expandCollisionReplacement(c.replacement, nil, len(c.find.GetGroupNumbers())-1, ""); err != nil {
		return nil, err
	}
	return c, nil
}

// Python replacements treat dollar signs literally and decimal backreferences
// as whole group numbers. Admission bounds and validates each replacement.
func expandCollisionReplacement(s string, m *regexp2.Match, groups int, identity string) (string, error) {
	s = strings.ReplaceAll(s, "{identity}", identity)
	var out strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] != '\\' {
			out.WriteByte(s[i])
			continue
		}
		i++
		if i >= len(s) {
			return "", ErrUnsupportedProfile
		}
		switch s[i] {
		case '\\':
			out.WriteByte('\\')
		case 'n':
			out.WriteByte('\n')
		case 'r':
			out.WriteByte('\r')
		case 't':
			out.WriteByte('\t')
		default:
			start := i
			for i < len(s) && s[i] >= '0' && s[i] <= '9' {
				i++
			}
			if start == i {
				return "", ErrUnsupportedProfile
			}
			n, e := strconv.Atoi(s[start:i])
			i--
			if e != nil || n < 1 || n > groups {
				return "", ErrUnsupportedProfile
			}
			if m != nil {
				g := m.GroupByNumber(n)
				if g != nil {
					out.WriteString(g.String())
				}
			}
		}
	}
	return out.String(), nil
}
func collisionQuoted(s string) string {
	const digits = "0123456789ABCDEF"
	var out strings.Builder
	for _, b := range []byte(s) {
		if b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' || b >= '0' && b <= '9' || strings.ContainsRune("-_.~", rune(b)) {
			out.WriteByte(b)
		} else {
			out.WriteByte('%')
			out.WriteByte(digits[b>>4])
			out.WriteByte(digits[b&15])
		}
	}
	return out.String()
}
func collisionScalar(v any) (string, bool) {
	switch x := v.(type) {
	case string:
		return x, true
	case json.Number:
		s := x.String()
		if strings.ContainsAny(s, ".eE") {
			return "", false
		}
		n, ok := new(big.Int).SetString(s, 10)
		if !ok {
			return "", false
		}
		return n.String(), true
	case int:
		return strconv.Itoa(x), true
	case int64:
		return strconv.FormatInt(x, 10), true
	}
	return "", false
}

func (r *FeedURLRules) RewriteCollision(source string, metadata map[string]any, urlOnly bool) (string, FeedCollisionRank, error) {
	if !r.HasCollision() {
		return r.Rewrite(source), FeedCollisionRank{}, nil
	}
	c := r.collision
	rank := FeedCollisionRank{URL: source, Metadata: len(c.preferredMetadata), Source: len(c.preferredSource)}
	identityReplacement := ""
	if strings.Contains(c.replacement, "{identity}") {
		s, ok := metadata[c.identityKey].(string)
		if !ok || s == "" || urlOnly {
			return "", rank, ErrProviderClassification
		}
		identityReplacement = collisionQuoted(s)
	}
	var replacementErr error
	result, err := c.find.ReplaceFunc(source, func(m regexp2.Match) string {
		s, e := expandCollisionReplacement(c.replacement, &m, len(c.find.GetGroupNumbers())-1, identityReplacement)
		if e != nil {
			replacementErr = e
		}
		return s
	}, -1, -1)
	if err != nil || replacementErr != nil {
		return "", rank, ErrProviderClassification
	}
	if len(result) > 8192 || strings.ContainsAny(result, "\x00\r\n") {
		return "", rank, ErrProviderClassification
	}
	if urlOnly {
		return result, rank, nil
	}
	canonical, err := c.canonical.FindStringMatch(result)
	if err != nil || canonical == nil || len(canonical.GroupByNumber(1).Captures) == 0 {
		return "", rank, ErrProviderClassification
	}
	var identity string
	var ok bool
	if c.sourceIdentity != nil {
		m, e := c.sourceIdentity.FindStringMatch(source)
		if e != nil || m == nil || len(m.GroupByNumber(1).Captures) == 0 {
			return "", rank, ErrProviderClassification
		}
		identity, ok = m.GroupByNumber(1).String(), true
	} else {
		identity, ok = collisionScalar(metadata[c.identityKey])
	}
	if !ok || identity != canonical.GroupByNumber(1).String() {
		return "", rank, ErrProviderClassification
	}
	for i, re := range c.preferredSource {
		match, e := re.MatchString(source)
		if e != nil {
			return "", rank, ErrProviderClassification
		}
		if match {
			rank.Source = i
			break
		}
	}
	if value, ok := metadata[c.preferenceKey].(string); ok {
		for i, re := range c.preferredMetadata {
			match, e := re.MatchString(value)
			if e != nil {
				return "", rank, ErrProviderClassification
			}
			if match {
				rank.Metadata = i
				break
			}
		}
	}
	return result, rank, nil
}
