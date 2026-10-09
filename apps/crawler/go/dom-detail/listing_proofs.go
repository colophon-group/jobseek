package dom

import (
	"errors"
	"net/url"
	"strconv"
	"strings"
	"unicode"

	"github.com/andybalholm/cascadia"
	"github.com/dlclark/regexp2/v2"
	"golang.org/x/net/html"
	"golang.org/x/text/cases"
)

var ErrListingProof = errors.New("DOM configured inventory proof failed")

type ListingEmptyState struct {
	Selector, Text, RequiredSelector, ForbiddenSelector string
	Exact                                               bool
	RequiredPattern                                     *regexp2.Regexp
}

type ListingProofs struct {
	TotalSelector string
	TotalPattern  *regexp2.Regexp
	EmptyStates   []ListingEmptyState
}

func proofSelector(value any, required bool) (string, error) {
	if value == nil && !required {
		return "", nil
	}
	s, ok := value.(string)
	if !ok || strings.TrimSpace(s) == "" || len([]rune(s)) > 256 || strings.ContainsRune(s, 0) {
		return "", ErrListingProof
	}
	s = strings.TrimSpace(s)
	if _, err := cascadia.Parse(s); err != nil {
		return "", ErrListingProof
	}
	return s, nil
}

func proofPattern(value any) (*regexp2.Regexp, error) {
	s, ok := value.(string)
	if !ok || s == "" || len([]rune(s)) > 1024 || strings.ContainsRune(s, 0) {
		return nil, ErrListingProof
	}
	re, err := CompileURLPattern(`\A(?:` + s + `)\Z`)
	if err != nil {
		return nil, ErrListingProof
	}
	return re, nil
}

func ListingProofOptions(config Object) (*ListingProofs, error) {
	if config["advertised_total"] == nil && config["empty_states"] == nil {
		return nil, nil
	}
	p := &ListingProofs{}
	if v := config["advertised_total"]; v != nil {
		m, err := object(v)
		if err != nil || len(m) != 2 || m["selector"] == nil || m["regex"] == nil {
			return nil, ErrListingProof
		}
		p.TotalSelector, err = proofSelector(m["selector"], true)
		if err != nil {
			return nil, err
		}
		p.TotalPattern, err = proofPattern(m["regex"])
		if err != nil || len(p.TotalPattern.GetGroupNumbers()) != 2 {
			return nil, ErrListingProof
		}
	}
	if v := config["empty_states"]; v != nil {
		rows, ok := v.([]any)
		if !ok || len(rows) < 1 || len(rows) > 4 {
			return nil, ErrListingProof
		}
		for _, v := range rows {
			m, err := object(v)
			if err != nil {
				return nil, ErrListingProof
			}
			for key := range m {
				if key != "selector" && key != "exact_text" && key != "contains_text" && key != "required_link_selector" && key != "required_link_url_pattern" && key != "forbidden_link_selector" {
					return nil, ErrListingProof
				}
			}
			_, exact := m["exact_text"]
			_, contains := m["contains_text"]
			if exact == contains {
				return nil, ErrListingProof
			}
			state := ListingEmptyState{Exact: exact}
			state.Selector, err = proofSelector(m["selector"], true)
			if err != nil {
				return nil, err
			}
			key := "contains_text"
			if exact {
				key = "exact_text"
			}
			state.Text, ok = m[key].(string)
			if !ok || strings.TrimSpace(state.Text) == "" || len([]rune(state.Text)) > 256 || strings.ContainsRune(state.Text, 0) {
				return nil, ErrListingProof
			}
			state.Text = strings.TrimSpace(state.Text)
			if (m["required_link_selector"] == nil) != (m["required_link_url_pattern"] == nil) {
				return nil, ErrListingProof
			}
			state.RequiredSelector, err = proofSelector(m["required_link_selector"], false)
			if err != nil {
				return nil, err
			}
			if state.RequiredSelector != "" {
				state.RequiredPattern, err = proofPattern(m["required_link_url_pattern"])
				if err != nil {
					return nil, err
				}
			}
			state.ForbiddenSelector, err = proofSelector(m["forbidden_link_selector"], false)
			if err != nil {
				return nil, err
			}
			p.EmptyStates = append(p.EmptyStates, state)
		}
	}
	return p, nil
}

func proofNodes(tree *html.Node, selector string) []*html.Node {
	compiled, err := cascadia.Parse(selector)
	if err != nil {
		return nil
	}
	return cascadia.QueryAll(tree, compiled)
}

func proofFullMatch(pattern *regexp2.Regexp, text string) (*regexp2.Match, error) {
	match, err := pattern.FindStringMatch(text)
	if err != nil {
		return nil, err
	}
	return match, nil
}

func proofDecimal(text string) (int, error) {
	if text == "" {
		return 0, ErrListingProof
	}
	out := strings.Builder{}
	for _, r := range text {
		if !unicode.Is(unicode.Nd, r) {
			return 0, ErrListingProof
		}
		found := false
		for _, a := range unicode.Nd.R16 {
			if r >= rune(a.Lo) && r <= rune(a.Hi) && (r-rune(a.Lo))%rune(a.Stride) == 0 {
				out.WriteByte(byte((r-rune(a.Lo))/rune(a.Stride)%10) + '0')
				found = true
				break
			}
		}
		if !found {
			for _, a := range unicode.Nd.R32 {
				if r >= rune(a.Lo) && r <= rune(a.Hi) && (r-rune(a.Lo))%rune(a.Stride) == 0 {
					out.WriteByte(byte((r-rune(a.Lo))/rune(a.Stride)%10) + '0')
					found = true
					break
				}
			}
		}
		if !found {
			return 0, ErrListingProof
		}
	}
	n, err := strconv.Atoi(out.String())
	if err != nil {
		return 0, ErrListingProof
	}
	return n, nil
}

func ValidateListingProofs(source string, c ListingConfig, jobCount int) error {
	p := c.Proofs
	if p == nil {
		return nil
	}
	if jobCount < 0 {
		return ErrListingProof
	}
	tree, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
	if err != nil {
		return err
	}
	text := func(node *html.Node) string { return strings.Join(strings.Fields(richRowsText(node, " ")), " ") }
	if p.TotalPattern != nil {
		total, err := listingAdvertisedTotalTree(tree, p)
		if err != nil || total == nil || *total != jobCount {
			return ErrListingProof
		}
	}

	matches := func(state ListingEmptyState) bool {
		nodes := proofNodes(tree, state.Selector)
		expected := strings.Join(strings.Fields(state.Text), " ")
		if state.Exact {
			for _, node := range nodes {
				if text(node) == expected {
					return true
				}
			}
			return false
		}
		return len(nodes) > 0 && strings.Contains(cases.Fold().String(text(nodes[0])), cases.Fold().String(expected))
	}
	for _, state := range p.EmptyStates {
		if state.ForbiddenSelector != "" && matches(state) && len(proofNodes(tree, state.ForbiddenSelector)) > 0 {
			return ErrListingProof
		}
	}
	if jobCount > 0 || len(p.EmptyStates) == 0 {
		return nil
	}
	base, err := url.Parse(c.BoardURL)
	if err != nil {
		return ErrListingProof
	}
	for _, state := range p.EmptyStates {
		if !matches(state) {
			continue
		}
		if state.RequiredSelector == "" {
			return nil
		}
		nodes := proofNodes(tree, state.RequiredSelector)
		if len(nodes) == 0 {
			continue
		}
		valid := true
		for _, node := range nodes {
			href := ""
			for _, a := range node.Attr {
				if a.Key == "href" {
					href = a.Val
					break
				}
			}
			if href == "" {
				valid = false
				break
			}
			absolute := ""
			if c.JoinProofURL != nil {
				absolute, err = c.JoinProofURL(c.BoardURL, href)
			} else {
				var u *url.URL
				u, err = url.Parse(href)
				if err == nil {
					absolute = base.ResolveReference(u).String()
				}
			}
			if err != nil {
				valid = false
				break
			}
			match, err := proofFullMatch(state.RequiredPattern, absolute)
			if err != nil {
				return err
			}
			if match == nil {
				valid = false
				break
			}
		}
		if valid {
			return nil
		}
	}
	return ErrListingProof
}

// A static paginator proves the declared root total against the complete
// deduplicated listing before independent JobPosting verification.
func ListingAdvertisedTotal(source string, p *ListingProofs) (*int, error) {
	if p == nil || p.TotalPattern == nil {
		return nil, nil
	}
	tree, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
	if err != nil {
		return nil, err
	}
	return listingAdvertisedTotalTree(tree, p)
}
func listingAdvertisedTotalTree(tree *html.Node, p *ListingProofs) (*int, error) {
	total := -1
	for _, node := range proofNodes(tree, p.TotalSelector) {
		match, err := proofFullMatch(p.TotalPattern, strings.Join(strings.Fields(richRowsText(node, " ")), " "))
		if err != nil {
			return nil, err
		}
		if match == nil {
			continue
		}
		count, err := proofDecimal(match.Groups()[1].String())
		if err != nil || total != -1 && total != count {
			return nil, ErrListingProof
		}
		total = count
	}
	if total < 0 {
		return nil, ErrListingProof
	}
	return &total, nil
}
