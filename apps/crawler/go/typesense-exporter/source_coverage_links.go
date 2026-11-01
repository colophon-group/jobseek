// Reviewed public career-page registry and transactional coverage revision binding.
package main

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5"
)

// This reviewed registry is separate from board_url, which can be an API or
// crawler endpoint. Absence means that no public career-page link is approved.
type sourceCoverageLinkInput struct {
	CompanySlug string
	BoardSlug   string
	URL         string
	Provenance  string
}

var sourceCoverageBoardSlug = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,159}$`)

func prepareSourceCoverageLinks(table registryTable) ([]sourceCoverageLinkInput, error) {
	want := []string{"company_slug", "board_slug", "public_url", "provenance"}
	if len(table.Columns) != len(want) {
		return nil, errors.New("source coverage link registry has wrong columns")
	}
	for i, column := range want {
		if table.Columns[i] != column {
			return nil, errors.New("source coverage link registry has wrong columns")
		}
	}
	if len(table.Rows) > 2000 {
		return nil, errors.New("source coverage link registry exceeds 2000 rows")
	}
	links := make([]sourceCoverageLinkInput, 0, len(table.Rows))
	seen := map[string]bool{}
	for i, row := range table.Rows {
		company, board := registryText(row, "company_slug"), registryText(row, "board_slug")
		rawURL, provenance := registryText(row, "public_url"), registryText(row, "provenance")
		if !sourceCoverageSlug.MatchString(company) || !sourceCoverageBoardSlug.MatchString(board) {
			return nil, fmt.Errorf("source coverage link row %d has invalid company or board slug", i+1)
		}
		key := company + "\x00" + board
		if seen[key] {
			return nil, fmt.Errorf("source coverage link row %d repeats a board", i+1)
		}
		seen[key] = true
		url, err := canonicalCoverageCareerURL(rawURL)
		if err != nil {
			return nil, fmt.Errorf("source coverage link row %d has unsafe public URL: %w", i+1, err)
		}
		if provenance != "employer-careers" && provenance != "ats-careers" && provenance != "reviewed-redirect" {
			return nil, fmt.Errorf("source coverage link row %d has invalid provenance", i+1)
		}
		links = append(links, sourceCoverageLinkInput{company, board, url, provenance})
	}
	return links, nil
}

func canonicalCoverageCareerURL(raw string) (string, error) {
	if strings.Contains(raw, "%") || strings.ContainsAny(raw, " \t\r\n") || strings.TrimSpace(raw) != raw {
		return "", errors.New("encoded or padded public career-page URL")
	}
	for _, r := range raw {
		if r < 0x20 || r >= 0x7f {
			return "", errors.New("non-ASCII or control character in public career-page URL")
		}
	}
	url, err := canonicalCoverageURL(raw)
	if err != nil {
		return "", errors.New("invalid public HTTPS career-page URL")
	}
	if strings.Contains(url, "%") {
		return "", errors.New("public career-page URL contains encoded characters")
	}
	return url, nil
}

func stageSourceCoverageLinks(ctx context.Context, tx pgx.Tx, links []sourceCoverageLinkInput) error {
	companies, boards := make([]string, 0, len(links)), make([]string, 0, len(links))
	for _, link := range links {
		companies = append(companies, link.CompanySlug)
		boards = append(boards, link.BoardSlug)
	}
	rows, err := tx.Query(ctx, `SELECT requested.company_slug,requested.board_slug,b.id::text,b.company_id::text
FROM unnest($1::text[], $2::text[]) AS requested(company_slug,board_slug)
LEFT JOIN company c ON c.slug=requested.company_slug
LEFT JOIN job_board b ON b.company_id=c.id AND b.board_slug=requested.board_slug`, companies, boards)
	if err != nil {
		return err
	}
	type identity struct{ BoardID, CompanyID string }
	resolved := map[string]identity{}
	for rows.Next() {
		var company, board string
		var boardID, companyID *string
		if err = rows.Scan(&company, &board, &boardID, &companyID); err != nil {
			break
		}
		if boardID == nil || companyID == nil {
			err = fmt.Errorf("approved public link has no matching company board: %s/%s", company, board)
			break
		}
		key := company + "\x00" + board
		if _, exists := resolved[key]; exists {
			err = fmt.Errorf("approved public link resolves to multiple boards: %s/%s", company, board)
			break
		}
		resolved[key] = identity{*boardID, *companyID}
	}
	rows.Close()
	if err != nil {
		return err
	}
	if err = rows.Err(); err != nil {
		return err
	}
	if len(resolved) != len(links) {
		return errors.New("approved public link registry did not resolve every board")
	}
	ids, companyIDs, urls, provenance := []string{}, []string{}, []string{}, []string{}
	for _, link := range links {
		id := resolved[link.CompanySlug+"\x00"+link.BoardSlug]
		ids = append(ids, id.BoardID)
		companyIDs = append(companyIDs, id.CompanyID)
		urls = append(urls, link.URL)
		provenance = append(provenance, link.Provenance)
	}
	_, err = tx.Exec(ctx, `INSERT INTO source_coverage_public_link
    (board_id,company_id,public_url,provenance)
SELECT board_id,company_id,public_url,provenance
FROM unnest($1::uuid[], $2::uuid[], $3::text[], $4::text[])
  AS link(board_id,company_id,public_url,provenance)
ON CONFLICT (board_id) DO UPDATE SET
    company_id=EXCLUDED.company_id,
    public_url=EXCLUDED.public_url,
    provenance=EXCLUDED.provenance,
    approved_at=clock_timestamp()
WHERE (source_coverage_public_link.company_id,source_coverage_public_link.public_url,source_coverage_public_link.provenance)
  IS DISTINCT FROM (EXCLUDED.company_id,EXCLUDED.public_url,EXCLUDED.provenance)`, ids, companyIDs, urls, provenance)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `DELETE FROM source_coverage_public_link
WHERE board_id <> ALL($1::uuid[])`, ids)
	return err
}
