package apisniffer

import (
	"encoding/json"
	"math/big"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

var tupuListingPath = regexp.MustCompile(`(?i)^/[a-z0-9_-]+/position/index$`)
var tupuListingID = regexp.MustCompile(`^[a-f0-9]{24}$`)
var tupuRecruitment = regexp.MustCompile(`^[A-Z]{1,32}RECRUITMENT$`)
var tupuCount = regexp.MustCompile(`^[0-9]{1,5}$`)
var tupuLocation = regexp.MustCompile(`^工作城市[：:]\s*(.+)$`)
var tupuPosted = regexp.MustCompile(`^发布于[：:]\s*([0-9]{4}-[0-9]{2}-[0-9]{2})$`)
var tupuSalary = regexp.MustCompile(`^薪资待遇[：:]\s*([0-9]+)-([0-9]+)$`)

// Tupu360ListingURL admits only the original unfiltered public listing root.
func Tupu360ListingURL(source string) bool {
	u, err := url.Parse(source)
	return err == nil && u.Scheme == "https" && u.User == nil && u.Port() == "" && u.RawQuery == "" && !u.ForceQuery && u.Fragment == "" && !strings.Contains(source, "#") && u.Opaque == "" && strings.HasSuffix(strings.ToLower(u.Hostname()), ".tupu360.com") && tupuListingPath.MatchString(u.Path) && u.RawPath == ""
}

// ParseTupu360Listing preserves the original strict card/count contract. It
// supplies no partial inventory on malformed cards or layout drift.
func ParseTupu360Listing(source, board string) (int, []map[string]any, error) {
	if !Tupu360ListingURL(board) {
		return 0, nil, ErrOptions
	}
	tree, err := finalDetailTree(source)
	if err != nil {
		return 0, nil, err
	}
	markers := inlineSelectedNodes(tree, "#position-list[positioncount]")
	if len(markers) != 1 {
		return 0, nil, ErrOptions
	}
	raw, _ := lastDetailAttribute(markers[0], "positioncount")
	total, err := strconv.Atoi(raw)
	if err != nil || !tupuCount.MatchString(raw) || total > 5000 {
		return 0, nil, ErrOptions
	}
	jobs := []map[string]any{}
	for _, row := range inlineSelectedNodes(tree, "#position-list .position-item") {
		id, _ := lastDetailAttribute(row, "pid")
		recruitment, _ := lastDetailAttribute(row, "recruitment")
		project, _ := lastDetailAttribute(row, "projectid")
		title := linkedInSelect(row, ".position-name .title .txt")
		location := linkedInSelect(row, ".e-city .txt")
		posted := linkedInSelect(row, ".time")
		if !tupuListingID.MatchString(id) || !tupuRecruitment.MatchString(recruitment) || project != "" || title == nil || location == nil || posted == nil {
			return 0, nil, ErrOptions
		}
		titleText := inlineNormalized(finalNodeText(title))
		city := tupuLocation.FindStringSubmatch(finalNodeText(location))
		date := tupuPosted.FindStringSubmatch(finalNodeText(posted))
		if titleText == "" || city == nil || date == nil {
			return 0, nil, ErrOptions
		}
		salaryText := finalNodeText(linkedInSelect(row, ".e-salary .txt"))
		var salary any
		if match := tupuSalary.FindStringSubmatch(salaryText); match != nil {
			// JSON numbers retain Python integer semantics through rich preparation.
			lower, upper := new(big.Int), new(big.Int)
			lower.SetString(match[1], 10)
			upper.SetString(match[2], 10)
			if lower.Cmp(upper) > 0 {
				return 0, nil, ErrOptions
			}
			salary = map[string]any{"min": json.Number(match[1]), "max": json.Number(match[2])}
		}
		query := "positionId=" + id + "&recruitmentType=" + recruitment
		jobs = append(jobs, map[string]any{"url": strings.TrimSuffix(board, "index") + "detail?" + query, "title": titleText, "locations": []string{strings.TrimSpace(city[1])}, "date_posted": date[1], "base_salary": salary, "language": "zh", "metadata": map[string]any{"position_id": id, "recruitment_type": recruitment, "salary_text": salaryText}})
	}
	if len(jobs) > 100 || len(jobs) > total {
		return 0, nil, ErrOptions
	}
	return total, jobs, nil
}
