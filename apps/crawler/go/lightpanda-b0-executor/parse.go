package executor

import (
	"encoding/json"
	"errors"
	publisherpolicy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/url"
	"regexp"
	"strings"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
)

var ErrBotChallenge = errors.New("rendered page is a bot challenge")

// ParseRendered consumes the held result exactly once without a subprocess or
// origin request. All mutable database identity checks remain the caller's job.
func ParseRendered(task b0task.Task, result *runtimev1.BrowserResult) (map[string]any, error) {
	if result != nil && result.GetSuccess() != nil && result.GetSuccess().ResourcePolicy == nil {
		return nil, publisherpolicy.ErrSignals
	}
	html, err := RenderedHTML(result, task.Envelope.SourceURL)
	if err != nil {
		return nil, err
	}
	value, err := b0task.ParseCanonicalValue(task.Envelope.ParserConfig)
	if err != nil {
		return nil, ErrProtocol
	}
	config, ok := value.(map[string]any)
	if !ok {
		return nil, ErrProtocol
	}
	var content map[string]any
	switch task.Envelope.ScraperType {
	case "dom":
		classification, err := dom.ClassifyRendered(html, config, result.GetSuccess().FinalUrl)
		if err != nil {
			return nil, err
		}
		switch classification["classification"] {
		case "gone":
			return nil, &NavigationHTTPError{RequestedURL: task.Envelope.SourceURL, ResponseURL: result.GetSuccess().FinalUrl, Status: 404}
		case "challenge":
			return nil, ErrBotChallenge
		case "okay":
		default:
			return nil, ErrRenderedResult
		}
		content, err = dom.Parse(html, config, &task.Envelope.SourceURL)
		if err != nil {
			return nil, err
		}
	case "json-ld":
		// Match the existing JSON-LD command's decoder at the package boundary.
		// Preserve the original typed defaults for the later common fill step.
		var commandConfig map[string]any
		if json.Unmarshal(task.Envelope.ParserConfig, &commandConfig) != nil {
			return nil, ErrProtocol
		}
		content, err = jsonld.Parse(task.Envelope.SourceURL, []byte(html), commandConfig)
		if err != nil {
			return nil, err
		}
	default:
		return nil, ErrProtocol
	}
	// The old package command crosses JSON IPC before JobContent construction.
	// Normalize direct package values through the same representation to retain
	// integer-versus-float/default coercion behavior without spawning a command.
	raw, err := json.Marshal(content)
	if err != nil {
		return nil, err
	}
	decoded, err := b0task.ParseCanonicalValue(raw)
	if err != nil {
		return nil, err
	}
	content, ok = decoded.(map[string]any)
	if !ok {
		return nil, ErrProtocol
	}
	applyContentDefaults(content, config)
	return content, nil
}

func applyContentDefaults(content, config map[string]any) {
	defaults, ok := config["defaults"].(map[string]any)
	if !ok {
		return
	}
	for _, field := range []string{"title", "description", "locations", "job_location_type", "employment_type", "date_posted", "base_salary", "language", "extras", "metadata"} {
		value, exists := defaults[field]
		if !exists {
			continue
		}
		current := content[field]
		empty := current == nil
		if list, ok := current.([]any); ok && len(list) == 0 {
			empty = true
		}
		if empty {
			content[field] = value
		}
	}
}

var avatureDetailPath = regexp.MustCompile(`(?i)/[^/]*careers[^/]*/(?:job|folder|pipeline)detail(?:/|$)`)
var avatureUniquePath = regexp.MustCompile(`(?i)/(?:folder|pipeline)detail(?:/|$)`)
var avatureVendorPath = regexp.MustCompile(`(?i)/(?:job|folder|pipeline)detail(?:/|$)`)

func avatureDetailURL(value string) bool {
	parsed, err := url.Parse(value)
	if err != nil {
		return false
	}
	return avatureDetailPath.MatchString(parsed.Path) || avatureUniquePath.MatchString(parsed.Path) || strings.HasSuffix(strings.ToLower(parsed.Hostname()), ".avature.net") && avatureVendorPath.MatchString(parsed.Path)
}

// FailureClass preserves disjoint gone/budget/transient policy. Invalid
// manifests, bot challenges and extraction errors never consume the budget.
func FailureClass(err error) FailureDisposition {
	var navigation *NavigationHTTPError
	if !errors.As(err, &navigation) {
		return FailureTransient
	}
	if navigation.PermanentGone() {
		return FailureGone
	}
	if navigation.Status == 406 && avatureDetailURL(navigation.ResponseURL) {
		return FailureTransient
	}
	if navigation.Status >= 400 && navigation.Status < 500 && navigation.Status != 401 && navigation.Status != 403 && navigation.Status != 429 {
		return FailureBudget
	}
	return FailureTransient
}
