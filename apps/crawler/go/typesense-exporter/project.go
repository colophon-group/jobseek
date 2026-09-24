package main

import (
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"
)

// Row contains the fields read by the existing job_posting CDC query. JSON
// numbers are decoded into their database-compatible int64/float64 forms.
type Row struct {
	ID                string     `json:"id"`
	CompanyID         string     `json:"company_id"`
	CompanyName       string     `json:"company_name"`
	CompanySlug       string     `json:"company_slug"`
	CompanyIcon       *string    `json:"company_icon"`
	Titles            []string   `json:"titles"`
	IsActive          bool       `json:"is_active"`
	LocationIDs       []int      `json:"location_ids"`
	LocationTypes     []string   `json:"location_types"`
	OccupationID      *int       `json:"occupation_id"`
	SeniorityID       *int       `json:"seniority_id"`
	TechnologyIDs     []int      `json:"technology_ids"`
	EmploymentType    string     `json:"employment_type"`
	ExperienceMin     *float64   `json:"experience_min"`
	ExperienceMax     *float64   `json:"experience_max"`
	Locales           []string   `json:"locales"`
	FirstSeenAt       *time.Time `json:"first_seen_at"`
	LastSeenAt        *time.Time `json:"last_seen_at"`
	SalaryEUR         *int64     `json:"salary_eur"`
	SalaryMin         *int64     `json:"salary_min"`
	SalaryMax         *int64     `json:"salary_max"`
	SalaryCurrency    *string    `json:"salary_currency"`
	SalaryPeriod      *string    `json:"salary_period"`
	SourceURL         *string    `json:"source_url"`
	DescriptionR2Hash *int64     `json:"description_r2_hash"`
}

type Maps struct {
	LocationNames         map[int]map[string]string `json:"location_names"`
	LocationFallbackNames map[int]string            `json:"location_fallback_names"`
	LocationTypes         map[int]string            `json:"location_types"`
	LocationAncestors     map[int][]int             `json:"location_ancestors"`
	OccupationNames       map[int]string            `json:"occupation_names"`
	OccupationAncestors   map[int][]int             `json:"occupation_ancestors"`
	SeniorityNames        map[int]string            `json:"seniority_names"`
	TechnologyNames       map[int]string            `json:"technology_names"`
}

const sortableAlphabet = "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz"

// candidateOrder reproduces typesense_candidate_order.py's 22-digit base-64
// key and signed int64 UUID halves. Nil values clear the optional sort index
// for inactive postings, matching the existing Typesense upsert semantics.
func candidateOrder(id string, active bool) (any, any, any, string, error) {
	compact := strings.ReplaceAll(id, "-", "")
	if len(compact) != 32 || strings.ToLower(compact) != compact {
		return nil, nil, nil, "", fmt.Errorf("invalid posting UUID %q", id)
	}
	bytes, err := hex.DecodeString(compact)
	if err != nil {
		return nil, nil, nil, "", err
	}
	if !active {
		return nil, nil, nil, compact[:2], nil
	}
	var digits [22]byte
	// Extract groups from the low end, exactly like repeated divmod(64).
	var buffer uint32
	var bits uint
	index := 21
	for i := 15; i >= 0; i-- {
		buffer |= uint32(bytes[i]) << bits
		bits += 8
		for bits >= 6 && index >= 0 {
			digits[index] = sortableAlphabet[buffer&63]
			buffer >>= 6
			bits -= 6
			index--
		}
	}
	if index >= 0 {
		digits[index] = sortableAlphabet[buffer&63]
		index--
	}
	for index >= 0 {
		digits[index] = sortableAlphabet[0]
		index--
	}
	hi, lo := uint64(0), uint64(0)
	for _, b := range bytes[:8] {
		hi = (hi << 8) | uint64(b)
	}
	for _, b := range bytes[8:] {
		lo = (lo << 8) | uint64(b)
	}
	return string(digits[:]), int64(hi ^ (uint64(1) << 63)), int64(lo ^ (uint64(1) << 63)), compact[:2], nil
}

// project maps one CDC row to the complete current Typesense document. It has
// no I/O, so the production exporter can compare it against a frozen Python
// projection before switching the cursor owner.
func project(row Row, maps Maps) (map[string]any, error) {
	if row.ID == "" || row.CompanyID == "" {
		return nil, errors.New("posting and company UUIDs are required")
	}
	key, hi, lo, bucket, err := candidateOrder(row.ID, row.IsActive)
	if err != nil {
		return nil, err
	}
	title := ""
	if len(row.Titles) > 0 {
		title = row.Titles[0]
	}
	locationNames := make([]string, 0, len(row.LocationIDs))
	geoTypes := make([]string, 0, len(row.LocationIDs))
	ancestorOnly := map[int]struct{}{}
	direct := map[int]struct{}{}
	for _, id := range row.LocationIDs {
		direct[id] = struct{}{}
	}
	for _, id := range row.LocationIDs {
		names := maps.LocationNames[id]
		name := names["en"]
		if name == "" && len(names) > 0 {
			// Python takes the first inserted locale. A Go map cannot retain
			// that order, so the taxonomy reader supplies the same fallback.
			name = maps.LocationFallbackNames[id]
			if name == "" {
				return nil, fmt.Errorf("missing location fallback name for %d", id)
			}
		}
		locationNames = append(locationNames, name)
		geoTypes = append(geoTypes, maps.LocationTypes[id])
		ancestors, ok := maps.LocationAncestors[id]
		if !ok {
			ancestors = []int{id}
		}
		for _, ancestor := range ancestors {
			if _, isDirect := direct[ancestor]; !isDirect {
				ancestorOnly[ancestor] = struct{}{}
			}
		}
	}
	locations := append([]int{}, row.LocationIDs...)
	additional := make([]int, 0, len(ancestorOnly))
	for id := range ancestorOnly {
		additional = append(additional, id)
	}
	sort.Ints(additional)
	locations = append(locations, additional...)
	technologyNames := make([]string, 0, len(row.TechnologyIDs))
	for _, id := range row.TechnologyIDs {
		technologyNames = append(technologyNames, maps.TechnologyNames[id])
	}
	locales := append([]string{}, row.Locales...)
	if len(locales) == 0 {
		locales = []string{"_none"}
	}
	legacyMin, legacyMax := -1, -1
	minYears, maxYears := -1.0, -1.0
	if row.ExperienceMin != nil {
		minYears = *row.ExperienceMin
		legacyMin = int(math.Ceil(minYears))
		maxYears, legacyMax = 99, 99
		if row.ExperienceMax != nil {
			maxYears = *row.ExperienceMax
			legacyMax = int(math.Floor(maxYears))
		}
	}
	firstSeen := int64(0)
	if row.FirstSeenAt != nil {
		firstSeen = row.FirstSeenAt.Unix()
	}
	doc := map[string]any{
		"id": row.ID, "candidate_order_key": key, "candidate_order_hi": hi,
		"candidate_order_lo": lo, "reconciliation_bucket": bucket,
		"company_id": row.CompanyID, "company_name": row.CompanyName,
		"company_slug": row.CompanySlug, "title": title, "is_active": row.IsActive,
		"has_content":  strings.TrimSpace(title) != "" && row.DescriptionR2Hash != nil,
		"location_ids": locations, "location_direct_ids": append([]int{}, row.LocationIDs...),
		"location_names": locationNames, "location_types": append([]string{}, row.LocationTypes...),
		"location_geo_types": geoTypes, "technology_ids": append([]int{}, row.TechnologyIDs...),
		"technology_names": technologyNames, "employment_type": row.EmploymentType,
		"experience_min": legacyMin, "experience_max": legacyMax,
		"experience_min_years": minYears, "experience_max_years": maxYears,
		"locales": locales, "first_seen_at": firstSeen,
	}
	if row.CompanyIcon != nil && *row.CompanyIcon != "" {
		doc["company_icon"] = *row.CompanyIcon
	}
	if row.OccupationID != nil {
		id := *row.OccupationID
		doc["occupation_id"] = id
		ancestors, ok := maps.OccupationAncestors[id]
		if !ok {
			ancestors = []int{id}
		}
		ordered := append([]int{}, ancestors...)
		sort.Ints(ordered)
		doc["occupation_ids"] = ordered
		if name, ok := maps.OccupationNames[id]; ok {
			doc["occupation_name"] = name
		}
	}
	if row.SeniorityID != nil {
		doc["seniority_id"] = *row.SeniorityID
		if name, ok := maps.SeniorityNames[*row.SeniorityID]; ok {
			doc["seniority_name"] = name
		}
	}
	if row.SalaryEUR != nil {
		doc["salary_eur"] = *row.SalaryEUR
	}
	if row.SalaryMin != nil {
		doc["salary_min"] = *row.SalaryMin
	}
	if row.SalaryMax != nil {
		doc["salary_max"] = *row.SalaryMax
	}
	if row.SalaryCurrency != nil && *row.SalaryCurrency != "" {
		doc["salary_currency"] = *row.SalaryCurrency
	}
	if row.SalaryPeriod != nil && *row.SalaryPeriod != "" {
		doc["salary_period"] = *row.SalaryPeriod
	}
	if row.SourceURL != nil && *row.SourceURL != "" {
		doc["source_url"] = *row.SourceURL
	}
	if row.LastSeenAt != nil {
		doc["last_seen_at"] = row.LastSeenAt.Unix()
	}
	return doc, nil
}
