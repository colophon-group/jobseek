package harness

import (
	"bufio"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

var metaTDMPattern = regexp.MustCompile(`(?is)<meta\s+[^>]*name\s*=\s*["']tdm-reservation["'][^>]*>`)
var contentPattern = regexp.MustCompile(`(?is)content\s*=\s*["']\s*([01])\s*["']`)

type RobotsRule struct {
	Allow   bool
	Pattern string
}

type RobotsPolicy struct {
	Rules []RobotsRule
}

func ParseRobots(body string) RobotsPolicy {
	var rules []RobotsRule
	active := false
	seenRule := false
	scanner := bufio.NewScanner(strings.NewReader(body))
	for scanner.Scan() {
		line := strings.TrimSpace(strings.SplitN(scanner.Text(), "#", 2)[0])
		if line == "" {
			if seenRule {
				active = false
				seenRule = false
			}
			continue
		}
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		key, value := strings.ToLower(strings.TrimSpace(parts[0])), strings.TrimSpace(parts[1])
		switch key {
		case "user-agent":
			if seenRule {
				active = false
				seenRule = false
			}
			if value == "*" {
				active = true
			}
		case "allow", "disallow":
			seenRule = true
			if active && value != "" {
				rules = append(rules, RobotsRule{Allow: key == "allow", Pattern: value})
			}
		}
	}
	return RobotsPolicy{Rules: rules}
}

func (p RobotsPolicy) Allows(path string) bool {
	bestLength := -1
	allowed := true
	for _, rule := range p.Rules {
		if robotsMatch(rule.Pattern, path) {
			length := len(strings.TrimSuffix(rule.Pattern, "$"))
			if length > bestLength || (length == bestLength && rule.Allow) {
				bestLength = length
				allowed = rule.Allow
			}
		}
	}
	return allowed
}

func robotsMatch(pattern, path string) bool {
	anchored := strings.HasSuffix(pattern, "$")
	pattern = strings.TrimSuffix(pattern, "$")
	quoted := regexp.QuoteMeta(pattern)
	quoted = strings.ReplaceAll(quoted, `\*`, `.*`)
	expression := "^" + quoted
	if anchored {
		expression += "$"
	}
	matched, err := regexp.MatchString(expression, path)
	return err == nil && matched
}

func TDMReserved(headerValue, html string) bool {
	headerValue = strings.TrimSpace(headerValue)
	if headerValue == "0" {
		return false
	}
	if headerValue == "1" {
		return true
	}
	for _, tag := range metaTDMPattern.FindAllString(html, -1) {
		match := contentPattern.FindStringSubmatch(tag)
		if len(match) == 2 && match[1] == "1" {
			return true
		}
	}
	return false
}

func ValidatePlan(plan *runtimev1.BrowserPlan, allowedOrigin string) (*runtimev1.BrowserResult, error) {
	if plan.GetContractVersion() != ContractVersion {
		return nil, fmt.Errorf("contract_version must be %q", ContractVersion)
	}
	target, err := url.Parse(plan.GetTargetUrl())
	if err != nil || target.Scheme == "" || target.Host == "" {
		return nil, errors.New("target_url must be an absolute URL")
	}
	origin := target.Scheme + "://" + target.Host
	if origin != allowedOrigin || target.Scheme != "http" || target.User != nil {
		return nil, errors.New("target_url is outside the synthetic fixture origin")
	}

	unsupported := map[runtimev1.BrowserCapability]struct{}{}
	for _, capability := range plan.GetRequiredCapabilities() {
		switch capability {
		case runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE:
		default:
			unsupported[capability] = struct{}{}
		}
	}
	if len(plan.GetActions()) > 0 {
		unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS] = struct{}{}
	}
	if len(plan.GetCaptures()) > 0 {
		unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_RESPONSE_CAPTURE] = struct{}{}
	}
	if len(plan.GetInterceptions()) > 0 {
		unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_REQUEST_INTERCEPTION] = struct{}{}
	}
	if navigation := plan.GetNavigation(); navigation != nil {
		if navigation.GetIgnoreTlsErrors() || len(navigation.GetHeaders()) > 0 {
			unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES] = struct{}{}
		}
		if navigation.GetWaitUntil() == runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE {
			unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES] = struct{}{}
		}
	}
	if session := plan.GetSession(); session != nil {
		if session.GetPersistent() {
			unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_PERSISTENT_SESSION] = struct{}{}
		}
		if session.GetHeadfulIdentity() {
			unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_HEADFUL_IDENTITY] = struct{}{}
		}
		if session.ProxyPolicyRef != nil {
			unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY] = struct{}{}
		}
		if session.SessionKey != nil {
			unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_PERSISTENT_SESSION] = struct{}{}
		}
	}
	for _, evaluation := range plan.GetEvaluations() {
		if evaluation.FrameName != nil {
			unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES] = struct{}{}
		}
		if evaluation.GetNetworkEffect() == runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT {
			unsupported[runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE] = struct{}{}
		}
	}
	if len(unsupported) > 0 {
		capabilities := make([]runtimev1.BrowserCapability, 0, len(unsupported))
		for capability := range unsupported {
			capabilities = append(capabilities, capability)
		}
		sort.Slice(capabilities, func(i, j int) bool { return capabilities[i] < capabilities[j] })
		return Unsupported(capabilities), nil
	}
	if len(plan.GetEvaluations()) > 0 && !hasCapability(plan, runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE) {
		return nil, errors.New("evaluations require BROWSER_CAPABILITY_EVALUATE")
	}
	if !hasCapability(plan, runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER) {
		return nil, errors.New("BROWSER_CAPABILITY_RENDER is required")
	}
	return nil, nil
}

func hasCapability(plan *runtimev1.BrowserPlan, wanted runtimev1.BrowserCapability) bool {
	for _, capability := range plan.GetRequiredCapabilities() {
		if capability == wanted {
			return true
		}
	}
	return false
}
