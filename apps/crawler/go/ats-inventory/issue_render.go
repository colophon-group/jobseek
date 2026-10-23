package main

import (
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"
)

func safeInventoryCode(value string) string {
	return strings.NewReplacer("`", "ˋ", "@", "@\u200b").Replace(value)
}
func boundedInventoryText(value string, limit int) string {
	value = strings.Join(strings.FieldsFunc(value, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }), " ")
	value = strings.Map(func(r rune) rune {
		if unicode.IsPrint(r) {
			return r
		}
		return -1
	}, value)
	value = pythonInventoryStrip(value)
	value = strings.ReplaceAll(value, "@", "@\u200b")
	if value == "" {
		return "Unnamed company"
	}
	if utf8.RuneCountInString(value) <= limit {
		return value
	}
	return strings.TrimRightFunc(string([]rune(value)[:limit-1]), unicode.IsSpace) + "…"
}
func safeInventoryTitle(value string, limit int) string {
	return strings.NewReplacer("`", "ˋ", "[", "［", "]", "］", "*", "∗", "_", "＿", "#", "＃", "<", "‹", ">", "›").Replace(boundedInventoryText(value, limit))
}
func renderCandidateIssue(plan candidatePlan, parentIssue int) (string, string, error) {
	c := plan.Candidate
	marker, e := candidateMarker(c.SourceKey, c.BoardURL)
	if e != nil {
		return "", "", e
	}
	title := "Add company: " + safeInventoryTitle(c.Name, 180)
	bodyName := safeInventoryCode(boundedInventoryText(c.Name, 300))
	impact := "unknown"
	if !c.ImpactUnknown {
		impact = fmt.Sprint(c.ActiveJobs)
	}
	countries := strings.Join(c.CountryCodes, ", ")
	if countries == "" {
		countries = "unknown"
	}
	lines := []string{
		marker,
		"### Company",
		"",
		"`" + bodyName + "` — `" + safeInventoryCode(c.BoardURL) + "`",
		"",
		"### Preconfigured board source",
		"",
		"- Source key: `" + safeInventoryCode(c.SourceKey) + "`",
		"- Upstream inventory family: `" + c.Family + "`",
		"- Jobseek-native ATS identity: `" + c.NativeATS + "`",
		"- Exact tenant: `" + safeInventoryCode(c.Tenant) + "`",
		"- Normalized board URL: `" + safeInventoryCode(c.BoardURL) + "`",
		"- Published active jobs: " + impact,
		"- Remote jobs: " + fmt.Sprint(c.RemoteJobs),
		"- Distinct locations: " + fmt.Sprint(c.LocationCount),
		"- Countries: " + countries,
		"",
		"The board is inventory evidence only. Use Jobseek-owned monitor and scraper code;",
		"do not import, execute, or add a runtime dependency on upstream scraper code.",
		"The simplified `ws` path should seed the compatible native monitor, then verify the",
		"company metadata, any additional official boards, live jobs, and normal quality gates.",
		"",
		"### Conservative deduplication context",
		"",
	}
	if len(plan.SoftWarnings) > 0 {
		for _, warning := range plan.SoftWarnings {
			lines = append(lines, "- **"+warning.Code+"**: "+warning.Summary)
			for _, ref := range warning.References {
				lines = append(lines, "  - `"+safeInventoryCode(ref)+"`")
			}
		}
	} else {
		lines = append(lines, "- No soft matches found in the bulk local/GitHub indexes.")
	}
	lines = append(lines, []string{
		"",
		"Soft matches require an explicit identity decision. Regional portals and operating",
		"divisions belong to the existing company by default. Preserve a separate company",
		"only when verification establishes an independent legal or employer identity;",
		"valid additional ATS sources should otherwise become boards on the existing company.",
		"",
		"Parent: #" + fmt.Sprint(parentIssue),
		"",
	}...)
	return title, strings.Join(lines, "\n"), nil
}
func renderSupportIssue(snapshot inventorySnapshot, family string) (string, string, error) {
	allowed := false
	for _, f := range snapshot.Coverage.UnsupportedFamilies {
		if f == family {
			allowed = true
		}
	}
	if !allowed {
		return "", "", fmt.Errorf("family is not unsupported")
	}
	rows := []inventoryRow{}
	for _, r := range snapshot.Rows {
		if r.ATS == family {
			rows = append(rows, r)
		}
	}
	representatives := append([]inventoryRow{}, rows...)
	sortInventoryRepresentatives(representatives)
	if len(representatives) > 5 {
		representatives = representatives[:5]
	}
	jobs := "unknown"
	if n := supportJobRows(snapshot.Manifest, family); n != nil {
		jobs = fmt.Sprint(*n)
	}
	title := "[ats inventory] Add native Jobseek support for " + family
	lines := []string{
		"<!-- ats-inventory-support:family=" + family + " -->",
		"## Outcome",
		"",
		"Add Jobseek-owned support for the newly observed `" + family + "` inventory family.",
		"The upstream project is an inventory/data source only: do not import or execute its",
		"scraper implementation and do not add it as a runtime dependency.",
		"",
		"## Current evidence",
		"",
		"- Upstream family: `" + family + "`",
		"- Company/tenant rows: " + fmt.Sprint(len(rows)),
		"- Published active job rows: " + jobs,
		"- Manifest: `" + snapshot.ManifestSHA256 + "`",
		"- Suggested reuse: start with the shared RSS, sitemap, DOM, nextdata, or API",
		"  monitor primitives and shared HTTP/retry/pagination helpers; add a dedicated thin",
		"  monitor only where the provider contract requires it.",
		"",
		"Representative inventory rows:",
		"",
	}
	for _, row := range representatives {
		lines = append(lines, "- `"+safeInventoryCode(row.Name)+"` — `"+safeInventoryCode(row.URL)+"`")
	}
	lines = append(lines, []string{
		"",
		"## Acceptance",
		"",
		"- Implement native Jobseek crawling with deterministic failure-mode tests.",
		"- Reuse base monitor/scraper machinery wherever practical.",
		"- Extend `ws` detection, selection, help, validation, scheduling, and tests.",
		"- Deliver this family in its own isolated-worktree PR from latest `origin/main`.",
		"- Obtain a fresh-context subagent review for codebase fit, simplification,",
		"  resilience, and performance; address findings and require green CI.",
		"- Keep companies in this family quarantined until the compatibility registry",
		"  points to the verified native monitor or preset.",
		"",
		"Parent: #6184",
		"",
	}...)
	return title, strings.Join(lines, "\n"), nil
}
