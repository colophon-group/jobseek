package workday

import (
	"bytes"
	"encoding/json"
	"errors"
	"regexp"
	"strings"
	"unicode"
)

// DetailContent is the Workday extractor's JobContent boundary. The common
// crawler enrichment and database writer still consume these fields.
type DetailContent struct {
	Title           *string           `json:"title"`
	Description     *string           `json:"description"`
	Locations       []string          `json:"locations"`
	EmploymentType  *string           `json:"employment_type"`
	JobLocationType *string           `json:"job_location_type"`
	DatePosted      *string           `json:"date_posted"`
	Metadata        map[string]string `json:"metadata"`
}

type workdayDetailEnvelope struct {
	JobPostingInfo json.RawMessage `json:"jobPostingInfo"`
}

type workdayPostingInfo struct {
	Title               *string         `json:"title"`
	JobDescription      *string         `json:"jobDescription"`
	Location            *string         `json:"location"`
	AdditionalLocations []string        `json:"additionalLocations"`
	Country             json.RawMessage `json:"country"`
	TimeType            *string         `json:"timeType"`
	RemoteType          *string         `json:"remoteType"`
	StartDate           *string         `json:"startDate"`
	JobReqID            *string         `json:"jobReqId"`
}

// ProjectDetail accepts one already fetched Workday detail response. It
// preserves HTML and raw timeType; shared enrichment normalizes those later.
func ProjectDetail(payload []byte, tenant string, tenantAliases []string) (DetailContent, error) {
	var envelope workdayDetailEnvelope
	if err := json.Unmarshal(payload, &envelope); err != nil {
		return DetailContent{}, err
	}
	if len(envelope.JobPostingInfo) == 0 || bytes.Equal(envelope.JobPostingInfo, []byte("null")) {
		return DetailContent{}, errors.New("Workday detail has no jobPostingInfo")
	}
	var info workdayPostingInfo
	if err := json.Unmarshal(envelope.JobPostingInfo, &info); err != nil {
		return DetailContent{}, err
	}
	if len(envelope.JobPostingInfo) < 2 || envelope.JobPostingInfo[0] != '{' {
		return DetailContent{}, errors.New("Workday jobPostingInfo is not an object")
	}
	content := DetailContent{
		Title:           info.Title,
		Description:     info.JobDescription,
		EmploymentType:  info.TimeType,
		JobLocationType: normalizeWorkdayLocationType(info.RemoteType),
		DatePosted:      info.StartDate,
	}
	country, err := workdayCountry(info.Country)
	if err != nil {
		return DetailContent{}, err
	}
	seen := make(map[string]struct{})
	locations := make([]string, 0, 1+len(info.AdditionalLocations))
	appendLocation := func(raw string) {
		if raw == "" {
			return
		}
		value := normalizeWorkdayLocation(raw, country, tenant, tenantAliases)
		if _, exists := seen[value]; !exists {
			seen[value] = struct{}{}
			locations = append(locations, value)
		}
	}
	if info.Location != nil {
		appendLocation(*info.Location)
	}
	for _, raw := range info.AdditionalLocations {
		appendLocation(raw)
	}
	if len(locations) > 0 {
		content.Locations = locations
	}
	if info.JobReqID != nil && *info.JobReqID != "" {
		content.Metadata = map[string]string{"jobReqId": *info.JobReqID}
	}
	return content, nil
}

func workdayCountry(raw json.RawMessage) (string, error) {
	if len(raw) == 0 || bytes.Equal(raw, []byte("null")) {
		return "", nil
	}
	if raw[0] == '"' {
		var value string
		if err := json.Unmarshal(raw, &value); err != nil {
			return "", err
		}
		return strings.TrimSpace(value), nil
	}
	if raw[0] != '{' {
		return "", errors.New("Workday country is not a string or object")
	}
	var object struct {
		Descriptor *string `json:"descriptor"`
	}
	if err := json.Unmarshal(raw, &object); err != nil {
		return "", err
	}
	if object.Descriptor == nil {
		return "", nil
	}
	return strings.TrimSpace(*object.Descriptor), nil
}

var (
	workdayTilde          = regexp.MustCompile(`\s*~.*`)
	workdayCode           = regexp.MustCompile(`^([A-Z]{2})-([A-Z]{2,3})-([A-Z][A-Z ]+)`)
	workdayBuilding       = regexp.MustCompile(`(?i)[-\s]+([A-Z]{0,4}\d+|BLDG).*$`)
	workdayFacilityPrefix = regexp.MustCompile(`(?i)^(?:(?:Store\s+)?M?\d+|Corporate Office)\s*[-–]\s*(.+)$`)
	workdayFacilityParts  = regexp.MustCompile(`\s*[-–]\s*`)
	workdayCityPostal     = regexp.MustCompile(`(?i)^(.+?)(?:,\s*|\s+)([A-Z]{2})\s+((?:\d{5}(?:-\d{4})?|[A-Z]\d[A-Z][ -]?\d[A-Z]\d))\s*$`)
	workdayFacilityWord   = regexp.MustCompile(`(?i)\b(?:mall|plaza|plz|center|centre|ctr|place|uptown|marketplace|shopping|shops?|commons?|comns|crossings?)\b`)
)

func normalizeWorkdayLocation(raw, country, tenant string, tenantAliases []string) string {
	cleaned := strings.TrimSpace(workdayTilde.ReplaceAllString(raw, ""))
	if cleaned == "" {
		return raw
	}
	if match := workdayCode.FindStringSubmatch(cleaned); match != nil {
		city := strings.TrimSpace(workdayBuilding.ReplaceAllString(match[3], ""))
		if city != "" {
			return titleWorkdayCodeCity(city) + ", " + match[2] + ", " + match[1]
		}
		return match[2] + ", " + match[1]
	}
	if match := workdayCityPostal.FindStringSubmatch(cleaned); match != nil {
		if city := workdayFacilityCity(match[1], tenant, tenantAliases); city != "" {
			parts := []string{city, strings.ToUpper(match[2])}
			if country != "" {
				parts = append(parts, country)
			}
			return strings.Join(parts, ", ")
		}
	}
	if strings.Contains(cleaned, "  ") {
		parts := make([]string, 0)
		for _, part := range strings.Split(cleaned, "  ") {
			if value := strings.TrimSpace(part); value != "" {
				parts = append(parts, value)
			}
		}
		return strings.Join(parts, ", ")
	}
	return cleaned
}

func titleWorkdayCodeCity(value string) string {
	var result []rune
	start := true
	for _, char := range strings.ToLower(value) {
		if start {
			char = unicode.ToUpper(char)
		}
		result = append(result, char)
		start = !unicode.IsLetter(char) && !unicode.IsDigit(char)
	}
	return string(result)
}

func workdayFacilityCity(head, tenant string, aliases []string) string {
	match := workdayFacilityPrefix.FindStringSubmatch(head)
	if match == nil {
		return ""
	}
	parts := workdayFacilityParts.Split(match[1], -1)
	for index, part := range parts {
		parts[index] = strings.TrimSpace(part)
		if parts[index] == "" {
			return ""
		}
	}
	separators := map[string]struct{}{}
	if tenant != "" {
		separators[strings.ToLower(tenant)] = struct{}{}
	}
	for _, alias := range aliases {
		separators[strings.ToLower(alias)] = struct{}{}
	}
	last := -1
	for index, part := range parts {
		if _, ok := separators[strings.ToLower(part)]; ok {
			last = index
		}
	}
	if last >= 0 {
		if last == len(parts)-1 {
			return ""
		}
		return strings.TrimSpace(strings.Join(parts[last+1:], "-"))
	}
	last = -1
	for index, part := range parts {
		if workdayFacilityWord.MatchString(part) {
			last = index
		}
	}
	if last < 0 || last != len(parts)-2 {
		return ""
	}
	return parts[last+1]
}

var workdayLocationTypes = map[string]string{
	"onsite": "onsite", "on-site": "onsite", "on-site working": "onsite", "onsite working": "onsite",
	"on_site": "onsite", "on site": "onsite", "office": "onsite", "in-office": "onsite",
	"in_office": "onsite", "in office": "onsite", "on-premises": "onsite", "in-person": "onsite",
	"in person": "onsite", "no": "onsite", "remotework_none": "onsite", "vor ort": "onsite",
	"büro": "onsite", "sur site": "onsite", "sur place": "onsite", "présentiel": "onsite",
	"en présentiel": "onsite", "bureau": "onsite", "geen afstandswerk": "onsite", "in sede": "onsite",
	"in ufficio": "onsite", "in loco": "onsite",
	"remote": "remote", "remote working": "remote", "telecommute": "remote", "work from home": "remote",
	"wfh": "remote", "fully remote": "remote", "100% remote": "remote", "fulltime": "remote",
	"remotework_full": "remote", "homeoffice": "remote", "home office": "remote", "fernarbeit": "remote",
	"remote arbeit": "remote", "télétravail": "remote", "à distance": "remote", "travail à distance": "remote",
	"volledig afstandswerk": "remote", "da remoto": "remote", "lavoro da remoto": "remote",
	"telelavoro": "remote", "lavoro a distanza": "remote",
	"hybrid": "hybrid", "hybrid working": "hybrid", "office, remote": "hybrid", "remote, office": "hybrid",
	"office/remote": "hybrid", "remote/office": "hybrid", "flexible": "hybrid", "partially remote": "hybrid",
	"partial": "hybrid", "punctual": "hybrid", "remotework_partial": "hybrid", "teilweise remote": "hybrid",
	"flexibel": "hybrid", "hybride": "hybrid", "travail à distance possible": "hybrid",
	"télétravail partiel": "hybrid", "gedeeltelijk afstandswerk": "hybrid", "afstandswerk mogelijk": "hybrid",
	"ibrido": "hybrid",
}

func normalizeWorkdayLocationType(raw *string) *string {
	if raw == nil {
		return nil
	}
	key := strings.ToLower(strings.TrimSpace(*raw))
	value, ok := workdayLocationTypes[key]
	if !ok && strings.HasSuffix(key, ")") {
		if start := strings.Index(key, " ("); start >= 0 {
			value, ok = workdayLocationTypes[key[:start]]
		}
	}
	if !ok {
		return nil
	}
	return &value
}
