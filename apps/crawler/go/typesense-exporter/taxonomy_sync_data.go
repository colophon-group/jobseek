package main

import (
	"context"
	"errors"
	"strconv"

	"github.com/jackc/pgx/v5"
)

const syncCompanySQL = `
SELECT c.id,c.name,c.slug,c.icon,c.logo,c.website,c.industry,
       c.employee_count_range,c.founded_year,i.name AS industry_name
FROM company c LEFT JOIN industry i ON i.id=c.industry ORDER BY c.id`
const syncCompanyDescriptionSQL = `SELECT company_id,locale,description FROM company_description ORDER BY company_id,locale`

type syncCompanyRow struct {
	ID           string `json:"id"`
	Name         string `json:"name"`
	Slug         string `json:"slug"`
	Icon         string `json:"icon"`
	Logo         string `json:"logo"`
	Website      string `json:"website"`
	Industry     *int   `json:"industry"`
	Employees    *int   `json:"employee_count_range"`
	Founded      *int   `json:"founded_year"`
	IndustryName string `json:"industry_name"`
}
type syncCompanyDescription struct {
	CompanyID   string `json:"company_id"`
	Locale      string `json:"locale"`
	Description string `json:"description"`
}

func buildSyncCompanies(rows []syncCompanyRow, descriptions []syncCompanyDescription, industries []taxonomyNameRow) ([]map[string]any, error) {
	names := map[int]map[string]string{}
	for _, row := range industries {
		if err := taxonomyDisplay(names, row.IndustryID, row.Locale, row.Name); err != nil {
			return nil, err
		}
	}
	localized := map[string]map[string]string{}
	for _, row := range descriptions {
		if localized[row.CompanyID] == nil {
			localized[row.CompanyID] = map[string]string{}
		}
		if old, exists := localized[row.CompanyID][row.Locale]; exists && old != row.Description {
			return nil, errors.New("conflicting company descriptions")
		}
		localized[row.CompanyID][row.Locale] = row.Description
	}
	docs := []map[string]any{}
	for _, row := range rows {
		doc := map[string]any{"id": row.ID, "name": row.Name, "slug": row.Slug}
		for field, value := range map[string]string{"icon": row.Icon, "logo": row.Logo, "website": row.Website} {
			if value != "" {
				doc[field] = value
			}
		}
		if row.Employees != nil {
			doc["employee_count_range"] = *row.Employees
		}
		if row.Founded != nil {
			doc["founded_year"] = *row.Founded
		}
		if text := localized[row.ID]["en"]; text != "" {
			doc["description"] = text
		}
		for _, locale := range []string{"de", "fr", "it"} {
			if text := localized[row.ID][locale]; text != "" {
				doc["description_"+locale] = text
			}
		}
		if row.Industry != nil {
			doc["industry_id"] = *row.Industry
			for _, locale := range []string{"de", "fr", "it"} {
				if name := names[*row.Industry][locale]; name != "" {
					doc["industry_name_"+locale] = name
				}
			}
		}
		if row.IndustryName != "" {
			doc["industry_name"] = row.IndustryName
		}
		docs = append(docs, doc)
	}
	return docs, nil
}
func loadSyncTaxonomySnapshot(ctx context.Context, conn *pgx.Conn, contract taxonomyContract) (taxonomyDocuments, error) {
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(context.Background())
	input, err := loadTaxonomyInputs(ctx, tx, contract)
	if err != nil {
		return nil, err
	}
	companies, err := readTaxonomyRows[syncCompanyRow](ctx, tx, syncCompanySQL)
	if err != nil {
		return nil, err
	}
	descriptions, err := readTaxonomyRows[syncCompanyDescription](ctx, tx, syncCompanyDescriptionSQL)
	if err != nil {
		return nil, err
	}
	docs, err := buildSyncTaxonomies(input, contract, companies, descriptions)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return docs, nil
}
func buildSyncTaxonomies(input taxonomyInputs, contract taxonomyContract, companies []syncCompanyRow, descriptions []syncCompanyDescription) (taxonomyDocuments, error) {
	docs, err := buildTaxonomyDocuments(input, contract)
	if err != nil {
		return nil, err
	}
	// Readiness canonicalizes whitespace for evidence; publishing retains the
	// original stored strings, as the existing sync producer does.
	locationRows := map[int]taxonomyLocationRow{}
	english := map[int]string{}
	for _, row := range input.Locations {
		locationRows[row.ID] = row
	}
	for _, row := range input.LocationNames {
		if row.Display && row.Locale == "en" {
			english[row.LocationID] = row.Name
		}
	}
	for _, doc := range docs["location"] {
		id := doc["location_id"].(int)
		doc["slug"] = locationRows[id].Slug
		doc["name_en"] = english[id]
	}
	for collection, rows := range map[string][]taxonomyNamedRow{"occupation": input.Occupations, "seniority": input.Seniorities, "technology": input.Technologies} {
		byID := map[int]taxonomyNamedRow{}
		for _, row := range rows {
			byID[row.ID] = row
		}
		for _, doc := range docs[collection] {
			id := doc[collection+"_id"].(int)
			row := byID[id]
			doc["slug"] = row.Slug
			if collection == "technology" && row.Name == "" {
				doc["name"] = row.Slug
			}
		}
	}
	docs["company"], err = buildSyncCompanies(companies, descriptions, input.IndustryNames)
	if err != nil {
		return nil, err
	}
	return docs, nil
}
func applySyncCounts(docs taxonomyDocuments, counts map[string]map[string]int, year map[string]int) {
	for collection, documents := range docs {
		for _, doc := range documents {
			id := doc["id"].(string)
			if collection != "company" {
				id = strconv.Itoa(doc[collection+"_id"].(int))
			}
			count := counts[collection][id]
			doc["active_posting_count"] = count
			if collection == "company" {
				doc["year_posting_count"] = year[id]
			} else {
				doc["has_active_postings"] = count > 0
			}
		}
	}
}
