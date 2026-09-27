package main

import (
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

//go:embed registry_sql.json
var registrySQLJSON []byte

type registryStatement struct {
	Key  string `json:"key"`
	Args []any  `json:"args"`
}
type registryPlan []registryStatement

func (p *registryPlan) add(key string, args ...any) {
	if args == nil {
		args = []any{}
	}
	*p = append(*p, registryStatement{key, args})
}
func registrySQL(key string) (string, error) {
	var contract map[string]string
	if err := json.Unmarshal(registrySQLJSON, &contract); err != nil {
		return "", errors.New("invalid embedded registry SQL")
	}
	sql, ok := contract[key]
	if !ok {
		return "", fmt.Errorf("unknown registry SQL: %s", key)
	}
	return sql, nil
}

func registryNames(table registryTable, locales []string, aliases bool) (slugs, namesSlugs, namesLocales, names []string, display []bool) {
	slugs = []string{}
	namesSlugs = []string{}
	namesLocales = []string{}
	names = []string{}
	display = []bool{}
	for _, row := range table.Rows {
		slug := registryText(row, "slug")
		slugs = append(slugs, slug)
		for _, locale := range locales {
			name := registryTrim(registryText(row, locale))
			if name != "" {
				namesSlugs = append(namesSlugs, slug)
				namesLocales = append(namesLocales, locale)
				names = append(names, name)
				display = append(display, true)
			}
		}
		if aliases {
			for _, name := range strings.Split(registryText(row, "aliases"), "|") {
				name = registryTrim(name)
				if name != "" {
					namesSlugs = append(namesSlugs, slug)
					namesLocales = append(namesLocales, "*")
					names = append(names, name)
					display = append(display, false)
				}
			}
		}
	}
	return
}
func registryTaxonomyPlan(tables map[string]registryTable) (registryPlan, error) {
	plan := registryPlan{}
	fixedLocales := []string{"en", "de", "fr", "it"}
	for _, kind := range []string{"occupation_domains", "occupations", "seniority"} {
		table := tables[kind]
		if len(table.Rows) == 0 {
			continue
		}
		locales := fixedLocales
		if kind == "occupations" {
			locales = []string{}
			for _, column := range table.Columns {
				if column != "slug" && column != "parent" && column != "domain" && column != "aliases" {
					locales = append(locales, column)
				}
			}
		}
		slugs, nameSlugs, nameLocales, names, display := registryNames(table, locales, kind != "occupation_domains")
		key := map[string]string{"occupation_domains": "OCCUPATION_DOMAIN", "occupations": "OCCUPATION", "seniority": "SENIORITY"}[kind]
		slugKey := "_UPSERT_" + key + "S"
		if kind == "seniority" {
			slugKey = "_UPSERT_SENIORITY"
		}
		plan.add(slugKey, slugs)
		if len(names) > 0 {
			plan.add("_UPSERT_"+key+"_NAMES", nameSlugs, nameLocales, names, display)
			if kind == "occupations" {
				plan.add("_DELETE_STALE_OCCUPATION_NAMES", nameSlugs, nameLocales, names)
			}
		}
		if kind == "occupations" {
			children, parents, domainOcc, domains := []string{}, []string{}, []string{}, []string{}
			for _, row := range table.Rows {
				if parent := registryTrim(registryText(row, "parent")); parent != "" {
					children = append(children, registryText(row, "slug"))
					parents = append(parents, parent)
				}
				if domain := registryTrim(registryText(row, "domain")); domain != "" {
					domainOcc = append(domainOcc, registryText(row, "slug"))
					domains = append(domains, domain)
				}
			}
			if len(children) > 0 {
				plan.add("_SET_OCCUPATION_PARENTS", children, parents)
				plan.add("_CLEAR_OCCUPATION_PARENTS", children)
			} else {
				plan.add("clear_all_occupation_parents")
			}
			if len(domainOcc) > 0 {
				plan.add("_SET_OCCUPATION_DOMAINS", domainOcc, domains)
			}
		}
	}
	if rows := tables["technologies"].Rows; len(rows) > 0 {
		slugs, names, categories := []*string{}, []*string{}, []*string{}
		for _, row := range rows {
			slugs = append(slugs, row["slug"])
			names = append(names, row["name"])
			categories = append(categories, row["category"])
		}
		plan.add("_UPSERT_TECHNOLOGIES", slugs, names, categories)
	}
	if rows := tables["industries"].Rows; len(rows) > 0 {
		ids, names, nameIDs, locales, values, display := []int64{}, []*string{}, []int64{}, []string{}, []string{}, []bool{}
		for _, row := range rows {
			idValue, err := registryOptionalInt(registryText(row, "id"))
			if err != nil || idValue == nil {
				return nil, errors.New("invalid industry ID")
			}
			id := *idValue
			name := row["en"]
			if name == nil || *name == "" {
				name = row["name"]
				if _, exists := row["name"]; !exists {
					empty := ""
					name = &empty
				}
			}
			ids = append(ids, id)
			names = append(names, name)
			for _, locale := range fixedLocales {
				value := registryTrim(registryText(row, locale))
				if value != "" {
					nameIDs = append(nameIDs, id)
					locales = append(locales, locale)
					values = append(values, value)
					display = append(display, true)
				}
			}
		}
		plan.add("_UPSERT_INDUSTRIES", ids, names)
		if len(values) > 0 {
			plan.add("_UPSERT_INDUSTRY_NAMES", nameIDs, locales, values, display)
		}
	}
	return plan, nil
}

var registryIntegerRE = regexp.MustCompile(`^[+-]?[0-9]+(?:_[0-9]+)*$`)

func registryOptionalInt(value string) (*int64, error) {
	value = registryTrim(value)
	var normalized strings.Builder
	for _, r := range value {
		if r > 127 && unicode.Is(unicode.Nd, r) {
			digit := -1
			for _, span := range unicode.Nd.R16 {
				if r >= rune(span.Lo) && r <= rune(span.Hi) && (r-rune(span.Lo))%rune(span.Stride) == 0 {
					digit = int((r-rune(span.Lo))/rune(span.Stride)) % 10
					break
				}
			}
			if digit < 0 {
				for _, span := range unicode.Nd.R32 {
					if r >= rune(span.Lo) && r <= rune(span.Hi) && (r-rune(span.Lo))%rune(span.Stride) == 0 {
						digit = int((r-rune(span.Lo))/rune(span.Stride)) % 10
						break
					}
				}
			}
			if digit >= 0 {
				normalized.WriteByte(byte('0' + digit))
				continue
			}
		}
		normalized.WriteRune(r)
	}
	value = normalized.String()
	if !registryIntegerRE.MatchString(value) {
		return nil, nil
	}
	parsed, err := strconv.ParseInt(strings.ReplaceAll(value, "_", ""), 10, 64)
	if err != nil {
		return nil, errors.New("registry integer exceeds database range")
	}
	return &parsed, nil
}
func registryCompanyPlan(companies, descriptions registryTable) (registryPlan, error) {
	plan := registryPlan{}
	if len(companies.Rows) > 0 {
		columns := make([][]*string, 6)
		for i := range columns {
			columns[i] = []*string{}
		}
		ints := make([][]*int64, 3)
		for i := range ints {
			ints[i] = []*int64{}
		}
		extras := []*string{}
		for _, row := range companies.Rows {
			for i, key := range []string{"slug", "name", "website", "logo_url", "icon_url", "logo_type"} {
				value := row[key]
				if i > 1 {
					value = registryOptional(row, key)
				}
				columns[i] = append(columns[i], value)
			}
			for i, key := range []string{"industry", "employee_count_range", "founded_year"} {
				value, err := registryOptionalInt(registryText(row, key))
				if err != nil {
					return nil, err
				}
				ints[i] = append(ints[i], value)
			}
			extra := registryOptional(row, "extras")
			if extra != nil && !json.Valid([]byte(*extra)) {
				extra = nil
			}
			extras = append(extras, extra)
		}
		plan.add("_UPSERT_COMPANIES", columns[0], columns[1], columns[2], columns[3], columns[4], columns[5], ints[0], ints[1], ints[2], extras)
	}
	slugs, locales, values := []string{}, []string{}, []string{}
	for _, row := range descriptions.Rows {
		for _, locale := range descriptions.Columns {
			if locale == "slug" {
				continue
			}
			value := registryTrim(registryText(row, locale))
			if value != "" {
				slugs = append(slugs, registryText(row, "slug"))
				locales = append(locales, locale)
				values = append(values, value)
			}
		}
	}
	if len(slugs) > 0 {
		plan.add("_UPSERT_COMPANY_DESCRIPTIONS", slugs, locales, values)
	}
	return plan, nil
}
