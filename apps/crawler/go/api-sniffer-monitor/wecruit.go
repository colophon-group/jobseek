package apisniffer

import (
	"regexp"
	"strings"
	"time"
)

var wecruitDatePrefix = regexp.MustCompile(`^([0-9]{4}-[0-9]{2}-[0-9]{2})(?:$|[ T])`)

func wecruitDateValue(value any) (string, error) {
	text := smallText(value)
	if text == "" {
		return "", nil
	}
	match := wecruitDatePrefix.FindStringSubmatch(text)
	if match == nil {
		return "", ErrInventory
	}
	date, err := time.Parse("2006-01-02", match[1])
	if err != nil || date.Year() < 1 {
		return "", ErrInventory
	}
	return match[1], nil
}

func wecruitTextHTML(value any) string {
	text, ok := value.(string)
	if !ok {
		return ""
	}
	// Python splitlines includes these separators in addition to LF/CRLF.
	text = strings.NewReplacer("\r\n", "\n", "\r", "\n", "\v", "\n", "\f", "\n", "\x1c", "\n", "\x1d", "\n", "\x1e", "\n", "\u0085", "\n", "\u2028", "\n", "\u2029", "\n").Replace(text)
	parts := []string{}
	for _, line := range strings.Split(text, "\n") {
		if line = strings.TrimSpace(line); line != "" {
			parts = append(parts, strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(line))
		}
	}
	if len(parts) == 0 {
		return ""
	}
	return "<p>" + strings.Join(parts, "<br>") + "</p>"
}

func WecruitJobFields(d *Document, origin, suite string, listing, detail map[string]any) (map[string]any, error) {
	id, err := d.String(listing["postId"])
	if err != nil {
		return nil, ErrInventory
	}
	lane, err := d.String(listing["recruitType"])
	identity := "wecruit:" + suite + ":" + id
	if err != nil || !explicitNextdataIdentity.MatchString(identity) {
		return nil, ErrInventory
	}
	titleValue := detail["postName"]
	if !detailTruthy(titleValue) {
		titleValue = listing["postName"]
	}
	title := smallText(titleValue)
	if title == "" {
		return nil, ErrInventory
	}
	sections := []string{}
	if text := wecruitTextHTML(detail["workContent"]); text != "" {
		sections = append(sections, "<h3>工作职责</h3>", text)
	}
	if text := wecruitTextHTML(detail["serviceCondition"]); text != "" {
		sections = append(sections, "<h3>任职要求</h3>", text)
	}
	if len(sections) == 0 {
		return nil, ErrInventory
	}
	locations := []string{}
	seen := map[string]bool{}
	if values, ok := detail["workPlaceList"].([]any); ok {
		for _, value := range values {
			if row, ok := value.(map[string]any); ok {
				if name := smallText(row["name"]); name != "" && !seen[name] {
					locations = append(locations, name)
					seen[name] = true
				}
			}
		}
	}
	if len(locations) == 0 {
		if text := smallText(detail["workPlaceStr"]); text != "" {
			locations = append(locations, text)
		}
	}
	if len(locations) == 0 {
		return nil, ErrInventory
	}
	extras := map[string]any{}
	for key, field := range map[string]string{"serviceCondition": "qualifications", "workContent": "responsibilities"} {
		if text := smallText(detail[key]); text != "" {
			extras[field] = text
		}
	}
	if text, err := wecruitDateValue(detail["endDate"]); err != nil {
		return nil, err
	} else if text != "" {
		extras["valid_through"] = text
	}
	metadata := map[string]any{}
	if value := listing["recruitType"]; value != nil && value != "" {
		metadata["recruit_type"] = value
	}
	for field, key := range map[string]string{"post_code": "postCode", "external_post_id": "externalPostId", "company": "company", "department": "department", "post_type": "postTypeName", "job_level": "jobLevel", "education": "education", "recruits": "recruitNumStr", "project": "projectName"} {
		value := detail[key]
		text, isString := value.(string)
		if value != nil && (!isString || text != "") {
			metadata[field] = value
		}
	}
	fields := map[string]any{
		"url":   origin + "/SU" + suite + "/pb/posDetail.html?postId=" + id + "&postType=" + lane,
		"title": title, "description": strings.Join(sections, "\n"), "locations": locations, "language": "zh",
		"source_identity": identity,
	}
	if len(metadata) > 0 {
		fields["metadata"] = metadata
	}
	if len(extras) > 0 {
		fields["extras"] = extras
	}
	dateValue := detail["publishDate"]
	if !detailTruthy(dateValue) {
		dateValue = listing["publishDate"]
	}
	if text, err := wecruitDateValue(dateValue); err != nil {
		return nil, err
	} else if text != "" {
		fields["date_posted"] = text
	}
	return fields, nil
}
