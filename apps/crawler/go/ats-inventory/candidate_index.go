package main

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"reflect"
	"sort"
	"strings"
	"unicode/utf8"
)

type dedupEvidence struct {
	Code       string   `json:"code"`
	Summary    string   `json:"summary"`
	References []string `json:"references"`
}
type candidatePlan struct {
	Candidate    inventoryCandidate `json:"candidate"`
	HardSkips    []dedupEvidence    `json:"hard_skips"`
	SoftWarnings []dedupEvidence    `json:"soft_warnings"`
}

func (p candidatePlan) eligible() bool { return len(p.HardSkips) == 0 }
func (p candidatePlan) report() map[string]any {
	decision := "eligible"
	if !p.eligible() {
		decision = "hard_skip"
	}
	return map[string]any{"decision": decision, "candidate": p.Candidate, "hard_skips": p.HardSkips, "soft_warnings": p.SoftWarnings}
}

type localInventoryCompany struct {
	Slug    string `json:"slug"`
	Name    string `json:"name"`
	Website string `json:"website"`
}

func (c localInventoryCompany) reference() string {
	suffix := ""
	if c.Website != "" {
		suffix = " (" + c.Website + ")"
	}
	return "company:" + c.Slug + " " + c.Name + suffix
}

type localInventoryBoard struct{ CompanySlug, BoardSlug, BoardURL, MonitorType string }

func (b localInventoryBoard) reference() string {
	return "board:" + b.BoardSlug + " (" + b.MonitorType + ", " + b.BoardURL + ")"
}

type inventoryReference interface{ reference() string }

func inventoryReferences[T inventoryReference](items []T) []string {
	set := map[string]bool{}
	for _, item := range items {
		set[item.reference()] = true
	}
	refs := sortedInventoryKeys(set)
	if len(refs) > 8 {
		hidden := len(refs) - 8
		refs = append(refs[:8], fmt.Sprintf("... and %d more", hidden))
	}
	return refs
}

type localRegistryIndex struct {
	BoardURLs                                                 map[string][]localInventoryBoard
	ATSTenants                                                map[[2]string][]localInventoryBoard
	CompanyNames, CompanySlugs, CompanyDomains, CompanyTokens map[string][]localInventoryCompany
}

func newLocalRegistryIndex() *localRegistryIndex {
	return &localRegistryIndex{BoardURLs: map[string][]localInventoryBoard{}, ATSTenants: map[[2]string][]localInventoryBoard{}, CompanyNames: map[string][]localInventoryCompany{}, CompanySlugs: map[string][]localInventoryCompany{}, CompanyDomains: map[string][]localInventoryCompany{}, CompanyTokens: map[string][]localInventoryCompany{}}
}
func readInventoryRegistryCSV(path string, visit func(map[string]string) error) error {
	file, e := os.Open(path)
	if e != nil {
		return e
	}
	defer file.Close()
	r := csv.NewReader(file)
	r.FieldsPerRecord = -1
	r.LazyQuotes = true
	header, e := r.Read()
	if e == io.EOF {
		return nil
	}
	if e != nil {
		return e
	}
	for _, s := range header {
		if !utf8.ValidString(s) {
			return fmt.Errorf("registry is not UTF8")
		}
	}
	for {
		row, e := r.Read()
		if e == io.EOF {
			return nil
		}
		if e != nil {
			return e
		}
		record := map[string]string{}
		for i, v := range row {
			if !utf8.ValidString(v) {
				return fmt.Errorf("registry is not UTF8")
			}
			if i < len(header) {
				record[header[i]] = v
			}
		}
		if e = visit(record); e != nil {
			return e
		}
	}
}
func loadLocalRegistryIndex(companiesPath, boardsPath string) (*localRegistryIndex, error) {
	index := newLocalRegistryIndex()
	registry, e := loadCompatibility()
	if e != nil {
		return nil, e
	}
	e = readInventoryRegistryCSV(companiesPath, func(raw map[string]string) error {
		c := localInventoryCompany{pythonInventoryStrip(raw["slug"]), pythonInventoryStrip(raw["name"]), pythonInventoryStrip(raw["website"])}
		if c.Slug == "" || c.Name == "" {
			return nil
		}
		name, slug := normalizeInventoryLabel(c.Name), normalizeInventoryLabel(c.Slug)
		index.CompanyNames[name] = append(index.CompanyNames[name], c)
		index.CompanySlugs[slug] = append(index.CompanySlugs[slug], c)
		if domain := inventoryWebsiteDomain(c.Website); domain != "" {
			index.CompanyDomains[domain] = append(index.CompanyDomains[domain], c)
		}
		for _, token := range inventoryLabelTokens(c.Name, c.Slug) {
			index.CompanyTokens[token] = append(index.CompanyTokens[token], c)
		}
		return nil
	})
	if e != nil {
		return nil, e
	}
	e = readInventoryRegistryCSV(boardsPath, func(raw map[string]string) error {
		rawURL, monitor := pythonInventoryStrip(raw["board_url"]), pythonInventoryStrip(raw["monitor_type"])
		if rawURL == "" || monitor == "" {
			return nil
		}
		boardURL, e := normalizeBoardURL(rawURL)
		if e != nil {
			return nil
		}
		b := localInventoryBoard{pythonInventoryStrip(raw["company_slug"]), pythonInventoryStrip(raw["board_slug"]), boardURL, monitor}
		index.BoardURLs[sha256Bytes([]byte(boardURL))] = append(index.BoardURLs[sha256Bytes([]byte(boardURL))], b)
		config := map[string]any{}
		if json.Unmarshal([]byte(raw["monitor_config"]), &config) != nil || config == nil {
			config = map[string]any{}
		}
		for family, c := range registry {
			proved, e := boardProvesInventoryCompatibility(monitor, config, c)
			if e != nil {
				return e
			}
			if !proved {
				continue
			}
			tenant := candidateTenantKey(family, boardURL, config)
			if tenant == "" {
				continue
			}
			native := nativeATSIdentity(family, c)
			key := [2]string{native, tenant}
			index.ATSTenants[key] = append(index.ATSTenants[key], b)
			allSites, boolean := config["all_sites"].(bool)
			if family == "workday" && !(boolean && !allSites) {
				if wildcard := workdayTenantWildcard(tenant); wildcard != "" {
					key = [2]string{native, wildcard}
					index.ATSTenants[key] = append(index.ATSTenants[key], b)
				}
			}
		}
		return nil
	})
	if e != nil {
		return nil, e
	}
	return index, nil
}
func boardProvesInventoryCompatibility(monitor string, config map[string]any, c compatibility) (bool, error) {
	if !c.candidateEligible() || c.MonitorType == nil || *c.MonitorType != monitor {
		return false, nil
	}
	if c.Kind == "native" {
		return true, nil
	}
	if !c.Seedable && c.MonitorConfigJSON == nil {
		return false, nil
	}
	expected, e := c.monitorConfig()
	if e != nil {
		return false, e
	}
	for key, value := range expected {
		if !reflect.DeepEqual(config[key], value) {
			return false, nil
		}
	}
	return true, nil
}
func (i *localRegistryIndex) hardEvidence(c inventoryCandidate) []dedupEvidence {
	out := []dedupEvidence{}
	if exact := i.BoardURLs[c.boardURLHash()]; len(exact) > 0 {
		out = append(out, dedupEvidence{"existing_board_url", "The normalized board URL is already configured.", inventoryReferences(exact)})
	}
	keys := [][2]string{{c.NativeATS, c.Tenant}}
	if c.Family == "workday" {
		if wildcard := workdayTenantWildcard(c.Tenant); wildcard != "" {
			keys = append(keys, [2]string{c.NativeATS, wildcard})
		}
	}
	boards := []localInventoryBoard{}
	for _, key := range keys {
		boards = append(boards, i.ATSTenants[key]...)
	}
	if len(boards) > 0 {
		out = append(out, dedupEvidence{"existing_ats_tenant", "The native ATS and exact tenant identity are already configured.", inventoryReferences(boards)})
	}
	return out
}
func inventoryLabelTokens(values ...string) []string {
	out := map[string]bool{}
	for _, value := range values {
		for _, token := range strings.Fields(normalizeInventoryLabel(value)) {
			if len(token) >= 3 {
				out[token] = true
			}
		}
	}
	return sortedInventoryKeys(out)
}
func inventoryTokenSet(values ...string) map[string]bool {
	out := map[string]bool{}
	for _, v := range inventoryLabelTokens(values...) {
		out[v] = true
	}
	return out
}
func strictInventorySubset(a, b map[string]bool) bool {
	if len(a) >= len(b) {
		return false
	}
	for key := range a {
		if !b[key] {
			return false
		}
	}
	return true
}
func sortLocalInventoryCompanies(values []localInventoryCompany) {
	sort.SliceStable(values, func(i, j int) bool {
		a, b := values[i], values[j]
		if unicodeFold(a.Name) != unicodeFold(b.Name) {
			return unicodeFold(a.Name) < unicodeFold(b.Name)
		}
		return a.Slug < b.Slug
	})
}
func (i *localRegistryIndex) relatedCompanies(c inventoryCandidate) []localInventoryCompany {
	tokens := inventoryTokenSet(c.Name, c.Slug)
	possible := map[string]localInventoryCompany{}
	for token := range tokens {
		matches := i.CompanyTokens[token]
		if len(matches) > 200 {
			continue
		}
		for _, company := range matches {
			possible[company.Slug] = company
		}
	}
	out := []localInventoryCompany{}
	name := normalizeInventoryLabel(c.Name)
	for _, company := range possible {
		other := normalizeInventoryLabel(company.Name)
		if min(len(name), len(other)) < 3 {
			continue
		}
		otherTokens := inventoryTokenSet(company.Name, company.Slug)
		if strings.Contains(name, other) || strings.Contains(other, name) || strictInventorySubset(tokens, otherTokens) || strictInventorySubset(otherTokens, tokens) {
			out = append(out, company)
		}
	}
	sortLocalInventoryCompanies(out)
	return out
}
func (i *localRegistryIndex) softEvidence(c inventoryCandidate) []dedupEvidence {
	out := []dedupEvidence{}
	name, slug := normalizeInventoryLabel(c.Name), normalizeInventoryLabel(c.Slug)
	exact := []localInventoryCompany{}
	if name != "" {
		exact = append(exact, i.CompanyNames[name]...)
	}
	if slug != "" {
		exact = append(exact, i.CompanySlugs[slug]...)
	}
	if len(exact) > 0 {
		out = append(out, dedupEvidence{"similar_company_identity", "A company name or slug matches; default to extending that company and create a separate identity only for a verified independent employer.", inventoryReferences(exact)})
	}
	exactRefs := map[string]bool{}
	for _, company := range exact {
		exactRefs[company.reference()] = true
	}
	related := []localInventoryCompany{}
	for _, company := range i.relatedCompanies(c) {
		if !exactRefs[company.reference()] {
			related = append(related, company)
		}
	}
	if len(related) > 0 {
		out = append(out, dedupEvidence{"possible_parent_or_region", "A related parent, subsidiary, or regional company name exists; geography alone is an additional board, not a new company identity.", inventoryReferences(related)})
	}
	if domain := inventoryWebsiteDomain(c.BoardURL); domain != "" {
		if matches := i.CompanyDomains[domain]; len(matches) > 0 {
			out = append(out, dedupEvidence{"shared_homepage_domain", "The board shares a registrable domain with existing company metadata.", inventoryReferences(matches)})
		}
	}
	return out
}

var inventoryLegalSuffixes = inventoryWords("ab ag bv co company corp corporation gmbh inc incorporated limited llc ltd nv oy plc sa sas sarl spa")
var inventoryGenericIdentityWords = inventoryWords("bank company global group health holding holdings international technology technologies tech")

func inventoryWords(text string) map[string]bool {
	out := map[string]bool{}
	for _, word := range strings.Fields(text) {
		out[word] = true
	}
	return out
}
func withoutInventoryLegalSuffix(label string) string {
	tokens := strings.Fields(label)
	for len(tokens) > 0 && inventoryLegalSuffixes[tokens[len(tokens)-1]] {
		tokens = tokens[:len(tokens)-1]
	}
	return strings.Join(tokens, " ")
}
func specificInventoryLeadingIdentity(a, b string) bool {
	if a == "" || b == "" || a == b {
		return false
	}
	if len(a) > len(b) {
		a, b = b, a
	}
	if !strings.HasPrefix(b, a+" ") {
		return false
	}
	for _, token := range strings.Fields(a) {
		if len(token) >= 3 && !inventoryGenericIdentityWords[token] {
			return true
		}
	}
	return false
}
func (i *localRegistryIndex) relatedIdentityMatches(c inventoryCandidate) []localInventoryCompany {
	name, slug := normalizeInventoryLabel(c.Name), normalizeInventoryLabel(c.Slug)
	matches := map[string]localInventoryCompany{}
	if name != "" {
		for _, company := range i.CompanyNames[name] {
			matches[company.Slug] = company
		}
	}
	if slug != "" {
		for _, company := range i.CompanySlugs[slug] {
			matches[company.Slug] = company
		}
	}
	brand := withoutInventoryLegalSuffix(name)
	for _, sameName := range i.CompanyNames {
		for _, company := range sameName {
			if specificInventoryLeadingIdentity(brand, withoutInventoryLegalSuffix(normalizeInventoryLabel(company.Name))) {
				matches[company.Slug] = company
			}
		}
	}
	out := []localInventoryCompany{}
	for _, company := range matches {
		out = append(out, company)
	}
	sortLocalInventoryCompanies(out)
	return out
}

type githubCandidateIndex struct {
	Items                                               []githubWorkItem
	SourceHashes, BoardHashes, TitleLabels, TitleTokens map[string][]githubWorkItem
}

func newGitHubCandidateIndex(items []githubWorkItem) *githubCandidateIndex {
	i := &githubCandidateIndex{Items: []githubWorkItem{}, SourceHashes: map[string][]githubWorkItem{}, BoardHashes: map[string][]githubWorkItem{}, TitleLabels: map[string][]githubWorkItem{}, TitleTokens: map[string][]githubWorkItem{}}
	for _, item := range items {
		i.add(item)
	}
	return i
}
func (i *githubCandidateIndex) add(item githubWorkItem) {
	i.Items = append(i.Items, item)
	for _, marker := range parseCandidateMarkers(item.Body) {
		key := sha256Bytes([]byte(marker.SourceKey))
		i.SourceHashes[key] = append(i.SourceHashes[key], item)
		i.BoardHashes[marker.BoardHash] = append(i.BoardHashes[marker.BoardHash], item)
	}
	if label := normalizeInventoryIssueTitle(item.Title); label != "" {
		i.TitleLabels[label] = append(i.TitleLabels[label], item)
		for _, token := range inventoryLabelTokens(label) {
			i.TitleTokens[token] = append(i.TitleTokens[token], item)
		}
	}
}
func (i *githubCandidateIndex) hardEvidence(c inventoryCandidate) []dedupEvidence {
	out := []dedupEvidence{}
	if items := i.SourceHashes[c.sourceHash()]; len(items) > 0 {
		out = append(out, dedupEvidence{"github_source_marker", "An import issue or active PR has the exact source marker.", inventoryReferences(items)})
	}
	if items := i.BoardHashes[c.boardURLHash()]; len(items) > 0 {
		out = append(out, dedupEvidence{"github_board_marker", "An import issue or active PR has the exact normalized board URL marker.", inventoryReferences(items)})
	}
	return out
}
func (i *githubCandidateIndex) softEvidence(c inventoryCandidate) []dedupEvidence {
	label := normalizeInventoryLabel(c.Name)
	matches := map[[2]string]githubWorkItem{}
	add := func(item githubWorkItem) { matches[[2]string{item.Kind, fmt.Sprint(item.Number)}] = item }
	for _, item := range i.TitleLabels[label] {
		add(item)
	}
	tokens := inventoryTokenSet(c.Name, c.Slug)
	for token := range tokens {
		items := i.TitleTokens[token]
		if len(items) > 200 {
			continue
		}
		for _, item := range items {
			title := normalizeInventoryIssueTitle(item.Title)
			otherTokens := inventoryTokenSet(title)
			if strings.Contains(title, label) || strings.Contains(label, title) || strictInventorySubset(tokens, otherTokens) || strictInventorySubset(otherTokens, tokens) {
				add(item)
			}
		}
	}
	if len(matches) == 0 {
		return []dedupEvidence{}
	}
	items := []githubWorkItem{}
	for _, item := range matches {
		items = append(items, item)
	}
	return []dedupEvidence{{"similar_github_title", "A company-request issue or active PR has a similar title; inspect it manually.", inventoryReferences(items)}}
}
func planInventoryCandidate(c inventoryCandidate, local *localRegistryIndex, github *githubCandidateIndex, ledger *candidateLedger) candidatePlan {
	hard := append(local.hardEvidence(c), github.hardEvidence(c)...)
	if record, ok := ledger.findSource(c.SourceKey); ok {
		hard = append(hard, dedupEvidence{"ledger_source_key", "The durable ledger already records this exact source (" + record.State + ").", record.References})
	}
	if records := ledger.findURL(c.BoardURL); len(records) > 0 {
		refs := map[string]bool{}
		for _, record := range records {
			for _, reference := range record.References {
				refs[reference] = true
			}
		}
		hard = append(hard, dedupEvidence{"ledger_board_url", "The durable ledger already records this exact normalized board URL.", sortedInventoryKeys(refs)})
	}
	soft := append(local.softEvidence(c), github.softEvidence(c)...)
	return candidatePlan{c, hard, soft}
}
