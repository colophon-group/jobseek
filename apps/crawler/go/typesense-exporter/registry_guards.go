package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

func registryReadonlyMount(directory, mountinfo string) bool {
	decode := strings.NewReplacer(`\040`, " ", `\011`, "\t", `\012`, "\n", `\134`, `\`)
	for _, line := range strings.Split(mountinfo, "\n") {
		fields := strings.Fields(strings.SplitN(line, " - ", 2)[0])
		if len(fields) < 6 {
			continue
		}
		if decode.Replace(fields[4]) != directory {
			continue
		}
		for _, option := range strings.Split(fields[5], ",") {
			if option == "ro" {
				return true
			}
		}
		return false
	}
	return false
}
func registryDataDirectory(source string) (string, error) {
	if source != "" {
		directory, err := filepath.Abs(source)
		if err != nil {
			return "", err
		}
		directory, err = filepath.EvalSymlinks(directory)
		if err != nil {
			return "", errors.New("source data directory unavailable")
		}
		if filepath.Base(directory) != "data" {
			return "", errors.New("source data must be the checkout data directory")
		}
		root := filepath.Dir(directory)
		for _, name := range []string{"pyproject.toml", "src/shared/constants.py", "go/typesense-exporter/go.mod"} {
			info, err := os.Stat(filepath.Join(root, name))
			if err != nil || !info.Mode().IsRegular() {
				return "", errors.New("source data directory is not a crawler checkout")
			}
		}
		return directory, nil
	}
	const directory = "/app/data"
	info, err := os.Stat(directory)
	if err != nil || !info.IsDir() {
		return "", errors.New("installed registry sync requires /app/data")
	}
	body, err := os.ReadFile("/proc/self/mountinfo")
	if err != nil || !registryReadonlyMount(directory, string(body)) {
		return "", errors.New("installed registry sync requires a separate read-only /app/data mount")
	}
	return directory, nil
}

func registryIndexExact(ctx context.Context, conn *pgx.Conn) (bool, bool, error) {
	sql, err := registrySQL("_LOCATION_LOOKUP_INDEX_STATE_SQL")
	if err != nil {
		return false, false, err
	}
	var valid, ready, unfiltered, nonunique bool
	var access, definition string
	err = conn.QueryRow(ctx, sql).Scan(&valid, &ready, &unfiltered, &nonunique, &access, &definition)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, false, nil
	}
	if err != nil {
		return false, false, err
	}
	definition = strings.Join(strings.Fields(strings.ToLower(definition)), " ")
	exact := valid && ready && unfiltered && nonunique && access == "btree" && strings.Contains(definition, " on public.location_name ") && strings.Contains(definition, "(lower(name)) include (location_id)")
	return exact, true, nil
}
func ensureRegistryLocationIndex(ctx context.Context, conn *pgx.Conn) (changed bool, runErr error) {
	var exists bool
	if err := conn.QueryRow(ctx, "SELECT to_regclass('public.location_name') IS NOT NULL").Scan(&exists); err != nil {
		return false, err
	}
	if !exists {
		return false, nil
	}
	const lock = "SELECT pg_advisory_lock(hashtext('jobseek:location-name-lower-lookup-index'))"
	if _, err := conn.Exec(ctx, lock); err != nil {
		return false, err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if _, err := conn.Exec(cleanup, "SELECT pg_advisory_unlock(hashtext('jobseek:location-name-lower-lookup-index'))"); err != nil && runErr == nil {
			runErr = errors.New("registry index lock release failed")
		}
	}()
	exact, present, err := registryIndexExact(ctx, conn)
	if err != nil || exact {
		return false, err
	}
	if present {
		if _, err = conn.Exec(ctx, "DROP INDEX CONCURRENTLY IF EXISTS idx_location_name_lower_lookup"); err != nil {
			return false, err
		}
	}
	ddl, err := registrySQL("_LOCATION_LOOKUP_INDEX_DDL")
	if err != nil {
		return false, err
	}
	if _, err = conn.Exec(ctx, ddl); err != nil {
		return false, err
	}
	exact, _, err = registryIndexExact(ctx, conn)
	if err != nil || !exact {
		return false, errors.New("registry location lookup index did not verify")
	}
	return true, nil
}
