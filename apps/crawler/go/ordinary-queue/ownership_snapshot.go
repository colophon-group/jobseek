package queue

import (
	"context"
	"sort"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

const ownershipSnapshotBatch = 256
const ownershipSnapshotByteLimit = 64 << 20

type boardConfigPair struct{ canonical, cached map[string]string }

func inspectMonitorConfigs(boardID string, canonical, cached map[string]string) (GreenhouseMonitorProfile, map[string]string, error) {
	profile, err := InspectRichMonitor(boardID, canonical)
	if err != nil {
		return GreenhouseMonitorProfile{}, nil, err
	}
	projected, err := InspectRichMonitor(boardID, cached)
	if err != nil {
		return GreenhouseMonitorProfile{}, nil, err
	}
	if projected.EffectiveConfigSHA256 != profile.EffectiveConfigSHA256 {
		return GreenhouseMonitorProfile{}, nil, ErrAuthorityLost
	}
	return projected, cached, nil
}

func inspectDetailConfigs(boardID string, canonical, cached map[string]string) (WorkdayDetailProfile, map[string]string, error) {
	profile, err := inspectDetailOwnership(boardID, canonical)
	if err != nil {
		return WorkdayDetailProfile{}, nil, err
	}
	projected, err := inspectDetailOwnership(boardID, cached)
	if err != nil {
		return WorkdayDetailProfile{}, nil, err
	}
	if projected.EffectiveBoardSHA256 != profile.EffectiveBoardSHA256 {
		return WorkdayDetailProfile{}, nil, ErrAuthorityLost
	}
	return projected, cached, nil
}

// Administrative snapshots remain inside the caller's existing transaction and
// lease/epoch barriers. One sorted SQL row-lock read and bounded Redis pipelines
// avoid a network round trip per board. No runtime claim caches this observation.
func (a *Authority) observeOwnershipConfigs(ctx context.Context, tx pgx.Tx, requested []string, retiring bool) (map[string]boardConfigPair, error) {
	if a == nil || a.queue == nil || tx == nil || len(requested) < 1 || len(requested) > 40000 {
		return nil, ErrConfiguration
	}
	seen := make(map[string]bool, len(requested))
	ids := make([]string, 0, len(requested))
	for _, id := range requested {
		if !canonicalUUID.MatchString(id) {
			return nil, ErrConfiguration
		}
		if !seen[id] {
			seen[id] = true
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	rows, err := tx.Query(ctx, `SELECT id::text,board_slug,board_url,crawler_type,company_id::text,
 COALESCE(metadata,'{}'::jsonb)::text,check_interval_minutes::text,
 scrape_interval_hours::text,COALESCE(throttle_key,''),monitor_needs_browser,scraper_needs_browser
 FROM public.job_board WHERE id=ANY($1::uuid[]) AND (is_enabled OR $2)
 AND (board_status IN ('active','suspect','quarantined','gone_pending','gone') OR $2)
 ORDER BY id FOR SHARE`, ids, retiring)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	pairs := make(map[string]boardConfigPair, len(ids))
	bytes := 0
	bounded := func(config map[string]string) bool {
		for key, value := range config {
			bytes += len(key) + len(value)
			if bytes > ownershipSnapshotByteLimit {
				return false
			}
		}
		return true
	}
	flag := func(value bool) string {
		if value {
			return "1"
		}
		return "0"
	}
	for rows.Next() {
		var id, slug, boardURL, kind, company, metadata, check, scrape, throttle string
		var monitorBrowser, scraperBrowser bool
		if err := rows.Scan(&id, &slug, &boardURL, &kind, &company, &metadata, &check, &scrape, &throttle, &monitorBrowser, &scraperBrowser); err != nil {
			return nil, err
		}
		canonical := map[string]string{"board_slug": slug, "board_url": boardURL, "crawler_type": kind, "company_id": company, "metadata": metadata, "check_interval_minutes": check, "scrape_interval_hours": scrape, "throttle_key": throttle, "domain": throttle, "monitor_needs_browser": flag(monitorBrowser), "scraper_needs_browser": flag(scraperBrowser)}
		if !bounded(canonical) {
			return nil, ErrConfiguration
		}
		pairs[id] = boardConfigPair{canonical: canonical}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	rows.Close()
	if len(pairs) != len(ids) {
		return nil, ErrUnsupportedProfile
	}
	for offset := 0; offset < len(ids); offset += ownershipSnapshotBatch {
		end := min(offset+ownershipSnapshotBatch, len(ids))
		pipe := a.queue.redis.Pipeline()
		commands := make([]*redis.MapStringStringCmd, 0, end-offset)
		for _, id := range ids[offset:end] {
			commands = append(commands, pipe.HGetAll(ctx, "board:"+id))
		}
		if _, err := pipe.Exec(ctx); err != nil {
			return nil, ErrObservation
		}
		for index, id := range ids[offset:end] {
			cached, err := commands[index].Result()
			if err != nil {
				return nil, ErrObservation
			}
			if !bounded(cached) {
				return nil, ErrConfiguration
			}
			pair := pairs[id]
			pair.cached = cached
			pairs[id] = pair
		}
	}
	return pairs, nil
}
