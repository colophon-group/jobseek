package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/signal"
	"reflect"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
)

const expectedLocationRows = 37526
const locationSlugConstraint = "chk_location_slug_nonblank"

type repairLocation struct {
	ID       int32
	Slug     *string
	Lat, Lng *float64
}
type locationRepairSummary struct {
	ExpectedRows                  int  `json:"expected_rows"`
	SourceRows                    int  `json:"source_rows"`
	LocalRows                     int  `json:"local_rows"`
	SourceCoordinatePairs         int  `json:"source_coordinate_pairs"`
	MissingSlugsBefore            int  `json:"missing_slugs_before"`
	MissingCoordinateValuesBefore int  `json:"missing_coordinate_values_before"`
	UpdatedRows                   int  `json:"updated_rows"`
	SourceLocalEqual              bool `json:"source_local_equal"`
	ConstraintValidated           bool `json:"constraint_validated"`
}

func readRepairLocations(ctx context.Context, tx pgx.Tx) ([]repairLocation, error) {
	rows, err := tx.Query(ctx, `SELECT id,slug,lat,lng FROM location ORDER BY id`)
	if err != nil {
		return nil, errors.New("location snapshot unavailable")
	}
	defer rows.Close()
	result := []repairLocation{}
	for rows.Next() {
		var r repairLocation
		if rows.Scan(&r.ID, &r.Slug, &r.Lat, &r.Lng) != nil {
			return nil, errors.New("location snapshot invalid")
		}
		result = append(result, r)
		if len(result) > expectedLocationRows {
			return nil, errors.New("location snapshot exceeds expected count")
		}
	}
	if rows.Err() != nil {
		return nil, errors.New("location snapshot read failed")
	}
	return result, nil
}

func validateRepairSource(rows []repairLocation, expected int) error {
	if len(rows) != expected {
		return errors.New("canonical location cardinality differs")
	}
	ids := map[int32]bool{}
	slugs := map[string]bool{}
	for _, r := range rows {
		if ids[r.ID] {
			return errors.New("canonical location IDs are not unique")
		}
		ids[r.ID] = true
		if r.Slug == nil || strings.TrimSpace(*r.Slug) == "" {
			return errors.New("canonical location source has blank slugs")
		}
		if slugs[*r.Slug] {
			return errors.New("canonical location source has duplicate slugs")
		}
		slugs[*r.Slug] = true
		if (r.Lat == nil) != (r.Lng == nil) {
			return errors.New("canonical location source has partial coordinate pairs")
		}
	}
	return nil
}

func validateRepairLocal(source, local []repairLocation) error {
	if len(source) != len(local) {
		return errors.New("canonical/local location ID sets differ")
	}
	for i, s := range source {
		l := local[i]
		if s.ID != l.ID {
			return errors.New("canonical/local location ID sets differ")
		}
		if l.Slug != nil && strings.TrimSpace(*l.Slug) != "" && (s.Slug == nil || *l.Slug != *s.Slug) ||
			l.Lat != nil && (s.Lat == nil || *l.Lat != *s.Lat) || l.Lng != nil && (s.Lng == nil || *l.Lng != *s.Lng) {
			return errors.New("populated local location values conflict with canonical source")
		}
	}
	return nil
}

func repairLocationTaxonomy(ctx context.Context, sourceConn, localConn *pgx.Conn, expected int) (locationRepairSummary, error) {
	fail := func(err error) (locationRepairSummary, error) { return locationRepairSummary{}, err }
	source, err := sourceConn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return fail(errors.New("canonical snapshot transaction unavailable"))
	}
	defer source.Rollback(context.Background())
	local, err := localConn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.Serializable})
	if err != nil {
		return fail(errors.New("local repair transaction unavailable"))
	}
	defer local.Rollback(context.Background())
	canonical, err := readRepairLocations(ctx, source)
	if err != nil {
		return fail(err)
	}
	if err = validateRepairSource(canonical, expected); err != nil {
		return fail(err)
	}
	for _, sql := range []string{`SET LOCAL statement_timeout = '5min'`, `SET LOCAL lock_timeout = '30s'`, `LOCK TABLE location IN SHARE ROW EXCLUSIVE MODE`} {
		if _, err = local.Exec(ctx, sql); err != nil {
			return fail(errors.New("local repair lock unavailable"))
		}
	}
	var exists bool
	if local.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='location'::regclass AND conname=$1 AND contype='c')`, locationSlugConstraint).Scan(&exists) != nil || !exists {
		return fail(errors.New("durable nonblank location slug constraint unavailable"))
	}
	before, err := readRepairLocations(ctx, local)
	if err != nil {
		return fail(err)
	}
	if err = validateRepairLocal(canonical, before); err != nil {
		return fail(err)
	}
	summary := locationRepairSummary{ExpectedRows: expected, SourceRows: len(canonical), LocalRows: len(before)}
	for _, r := range canonical {
		if r.Lat != nil {
			summary.SourceCoordinatePairs++
		}
	}
	for _, r := range before {
		if r.Slug == nil || strings.TrimSpace(*r.Slug) == "" {
			summary.MissingSlugsBefore++
		}
		if r.Lat == nil {
			summary.MissingCoordinateValuesBefore++
		}
		if r.Lng == nil {
			summary.MissingCoordinateValuesBefore++
		}
	}
	if _, err = local.Exec(ctx, `CREATE TEMP TABLE _location_taxonomy_repair (id INTEGER PRIMARY KEY,slug TEXT NOT NULL UNIQUE,lat REAL,lng REAL,CHECK ((lat IS NULL)=(lng IS NULL))) ON COMMIT DROP`); err != nil {
		return fail(errors.New("repair staging unavailable"))
	}
	rows := make([][]any, 0, len(canonical))
	for _, r := range canonical {
		rows = append(rows, []any{r.ID, r.Slug, r.Lat, r.Lng})
	}
	if n, err := local.CopyFrom(ctx, pgx.Identifier{"_location_taxonomy_repair"}, []string{"id", "slug", "lat", "lng"}, pgx.CopyFromRows(rows)); err != nil || n != int64(len(canonical)) {
		return fail(errors.New("repair staging failed"))
	}
	if local.QueryRow(ctx, `WITH updated AS (
	 UPDATE location AS local SET slug=CASE WHEN local.slug IS NULL OR btrim(local.slug)='' THEN source.slug ELSE local.slug END,
	 lat=COALESCE(local.lat,source.lat),lng=COALESCE(local.lng,source.lng)
	 FROM _location_taxonomy_repair AS source WHERE local.id=source.id AND (
	 ((local.slug IS NULL OR btrim(local.slug)='') AND source.slug IS NOT NULL) OR
	 (local.lat IS NULL AND source.lat IS NOT NULL) OR (local.lng IS NULL AND source.lng IS NOT NULL)) RETURNING local.id
	) SELECT count(*) FROM updated`).Scan(&summary.UpdatedRows) != nil {
		return fail(errors.New("location update failed"))
	}
	after, err := readRepairLocations(ctx, local)
	if err != nil {
		return fail(err)
	}
	if !reflect.DeepEqual(canonical, after) {
		return fail(errors.New("canonical/local equality proof failed"))
	}
	if _, err = local.Exec(ctx, `ALTER TABLE location VALIDATE CONSTRAINT chk_location_slug_nonblank`); err != nil {
		return fail(errors.New("location slug constraint validation failed"))
	}
	if local.QueryRow(ctx, `SELECT convalidated FROM pg_constraint WHERE conrelid='location'::regclass AND conname=$1`, locationSlugConstraint).Scan(&summary.ConstraintValidated) != nil || !summary.ConstraintValidated {
		return fail(errors.New("location slug constraint not validated"))
	}
	if err = local.Commit(ctx); err != nil {
		return fail(errors.New("location repair commit failed"))
	}
	if err = source.Commit(ctx); err != nil {
		return fail(errors.New("canonical snapshot completion failed"))
	}
	summary.SourceLocalEqual = true
	return summary, nil
}

func runLocationTaxonomyRepair() error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()
	localDSN, sourceDSN := os.Getenv("LOCAL_DATABASE_URL"), os.Getenv("WEB_DATABASE_URL")
	if localDSN == "" || sourceDSN == "" || localDSN == sourceDSN {
		return errors.New("separate local and canonical database configurations required")
	}
	role := os.Getenv("CRAWLER_DB_ROLE")
	if role == "" {
		role = "location-taxonomy-repair"
	}
	localConfig, err := providerCutoverPoolConfig(localDSN, role)
	if err != nil {
		return err
	}
	sourceConfig, err := providerCutoverPoolConfig(sourceDSN, role)
	if err != nil {
		return errors.New("invalid canonical database configuration")
	}
	sourceConfig.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:" + role + ":web"
	// The source snapshot may remain idle while the bounded target update runs.
	for _, c := range []*pgx.ConnConfig{localConfig.ConnConfig, sourceConfig.ConnConfig} {
		c.RuntimeParams["idle_in_transaction_session_timeout"] = "0"
	}
	local, err := pgx.ConnectConfig(ctx, localConfig.ConnConfig)
	if err != nil {
		return errors.New("local database unavailable")
	}
	defer local.Close(context.Background())
	source, err := pgx.ConnectConfig(ctx, sourceConfig.ConnConfig)
	if err != nil {
		return errors.New("canonical database unavailable")
	}
	defer source.Close(context.Background())
	summary, err := repairLocationTaxonomy(ctx, source, local, expectedLocationRows)
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", " ")
	return encoder.Encode(summary)
}
