package apisniffer

import (
	"regexp"
	"strconv"
	"strings"
)

type APIArrayCandidate struct {
	Path  string
	Items []map[string]any
}

var apiPathKey = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
var apiJobWords = regexp.MustCompile(`job|career|position|opening|vacanc|posting|requisition|listing|rolle|stellen`)
var apiTitleKey = regexp.MustCompile(`^(title|name|job_?title|position_?title|label|heading|role|job_?name|job_?opening_?name|title__c)$`)
var apiURLKey = regexp.MustCompile(`url|link|href|path|slug|uri|canonical|apply|detail`)

func apiIgnoreCase(value string) string {
	value = strings.NewReplacer("İ", "i", "ı", "i", "ſ", "s", "K", "k").Replace(value)
	return strings.ToLower(value)
}
func apiPathPart(key string) string {
	if apiPathKey.MatchString(key) {
		return key
	}
	return `"` + strings.ReplaceAll(strings.ReplaceAll(key, `\`, `\\`), `"`, `\"`) + `"`
}

// FindAPIArrayCandidates follows the original Python traversal order, including
// small wrapper arrays. The walk fails as a whole on resource limits.
func (d *Document) FindAPIArrayCandidates() ([]APIArrayCandidate, error) {
	if d == nil {
		return nil, ErrInventory
	}
	out := []APIArrayCandidate{}
	nodes := 0
	var walk func(any, string, int) error
	walk = func(value any, path string, depth int) error {
		nodes++
		if nodes > 200_000 || depth > 64 {
			return ErrInventory
		}
		switch value := value.(type) {
		case []any:
			items := []map[string]any{}
			for _, child := range value {
				if object, ok := child.(map[string]any); ok {
					items = append(items, object)
				}
			}
			if len(items) >= 3 {
				selected := path
				if selected == "" {
					selected = "$"
				}
				out = append(out, APIArrayCandidate{selected, items})
				if len(out) > 1000 {
					return ErrInventory
				}
			}
			if len(value) <= 50 {
				for index, child := range value {
					switch child.(type) {
					case map[string]any, []any:
						if err := walk(child, path+"["+strconv.Itoa(index)+"]", depth+1); err != nil {
							return err
						}
					}
				}
			}
		case map[string]any:
			for _, key := range d.ObjectKeys(value) {
				childPath := apiPathPart(key)
				if path != "" {
					childPath = path + "." + childPath
				}
				if err := walk(value[key], childPath, depth+1); err != nil {
					return err
				}
			}
		}
		return nil
	}
	if err := walk(d.Value, "", 0); err != nil {
		return nil, err
	}
	return out, nil
}

func apiArtworkKey(key string) bool {
	var letters strings.Builder
	for _, char := range apiIgnoreCase(key) {
		if char >= 'a' && char <= 'z' {
			letters.WriteRune(char)
		}
	}
	normalized := letters.String()
	for _, part := range []string{"image", "picture", "logo", "thumbnail", "avatar"} {
		if strings.Contains(normalized, part) {
			return true
		}
	}
	return false
}
func apiLooksLikeURL(value any) bool {
	text, ok := value.(string)
	return ok && len([]rune(text)) > 5 && (strings.HasPrefix(text, "http://") || strings.HasPrefix(text, "https://") || strings.HasPrefix(text, "/"))
}
func (d *Document) apiHasURLField(items []map[string]any) bool {
	if len(items) == 0 {
		return false
	}
	sample := items[:min(len(items), 5)]
	valid := func(path string) bool {
		for _, item := range sample {
			value, err := Search(item, path)
			if err != nil || !apiLooksLikeURL(value) {
				return false
			}
		}
		return true
	}
	var walk func(map[string]any, string, int) bool
	walk = func(object map[string]any, prefix string, depth int) bool {
		if depth > 64 {
			return false
		}
		for _, key := range d.ObjectKeys(object) {
			path := apiPathPart(key)
			if prefix != "" {
				path = prefix + "." + path
			}
			value := object[key]
			if child, ok := value.(map[string]any); ok {
				if walk(child, path, depth+1) {
					return true
				}
			} else if _, list := value.([]any); !list && apiURLKey.MatchString(apiIgnoreCase(key)) && !apiArtworkKey(key) && valid(path) {
				return true
			}
		}
		return false
	}
	if walk(sample[0], "", 0) {
		return true
	}
	for _, key := range d.ObjectKeys(sample[0]) {
		if !apiArtworkKey(key) && valid(apiPathPart(key)) {
			return true
		}
	}
	return false
}

func (d *Document) ScoreAPIArray(c APIArrayCandidate, endpoint string) int {
	score := 0
	if apiJobWords.MatchString(apiIgnoreCase(c.Path)) {
		score += 30
	}
	if apiJobWords.MatchString(apiIgnoreCase(endpoint)) {
		score += 5
	}
	title := false
	for _, item := range c.Items[:min(5, len(c.Items))] {
		for key := range item {
			title = title || apiTitleKey.MatchString(apiIgnoreCase(key))
		}
	}
	if title {
		score += 20
	}
	if d.apiHasURLField(c.Items) {
		score += 15
	}
	if len(c.Items) >= 3 {
		score += 5
	}
	return score
}

func (d *Document) SelectAPIArray(endpoint string) (*APIArrayCandidate, error) {
	candidates, err := d.FindAPIArrayCandidates()
	if err != nil || len(candidates) == 0 {
		return nil, err
	}
	best := candidates[0]
	score := d.ScoreAPIArray(best, endpoint)
	for _, candidate := range candidates[1:] {
		next := d.ScoreAPIArray(candidate, endpoint)
		if next > score || next == score && len(candidate.Items) > len(best.Items) {
			best, score = candidate, next
		}
	}
	return &best, nil
}
