package queue

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

// ObserveGreenhouseMonitor joins eligibility to an enabled, recoverable canonical
// board and its current Redis projection under the existing epoch barrier. It
// never pops, heartbeats or publishes work. The detached observation is not an
// active ownership plan; selection and writes must validate authority afresh.
// Enabled suspect/quarantine/gone states retain this same family owner so native
// lifecycle transitions cannot strand a member excluded from legacy claims.
func (a *Authority) ObserveGreenhouseMonitor(ctx context.Context, boardID string) (GreenhouseMonitorProfile, error) {
	if a == nil || a.queue == nil || a.pool == nil || !canonicalUUID.MatchString(boardID) {
		return GreenhouseMonitorProfile{}, ErrConfiguration
	}
	var result GreenhouseMonitorProfile
	err := a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, _, err = a.observeGreenhouseMonitor(ctx, tx, boardID)
		return err
	})
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return result, nil
}

func (a *Authority) observeGreenhouseMonitor(ctx context.Context, tx pgx.Tx, boardID string) (GreenhouseMonitorProfile, map[string]string, error) {
	var slug, boardURL, kind, company, metadata, check, scrape, throttle string
	var monitorBrowser, scraperBrowser bool
	err := tx.QueryRow(ctx, `SELECT board_slug,board_url,crawler_type,company_id::text,
   COALESCE(metadata,'{}'::jsonb)::text,check_interval_minutes::text,
   scrape_interval_hours::text,COALESCE(throttle_key,''),
   monitor_needs_browser,scraper_needs_browser
   FROM public.job_board WHERE id=$1::uuid AND is_enabled
   AND board_status IN ('active','suspect','quarantined','gone_pending','gone')
   FOR SHARE`, boardID).Scan(&slug, &boardURL, &kind, &company, &metadata, &check, &scrape, &throttle, &monitorBrowser, &scraperBrowser)
	if errors.Is(err, pgx.ErrNoRows) {
		return GreenhouseMonitorProfile{}, nil, ErrUnsupportedProfile
	}
	if err != nil {
		return GreenhouseMonitorProfile{}, nil, err
	}
	flag := func(value bool) string {
		if value {
			return "1"
		}
		return "0"
	}
	canonical := map[string]string{
		"board_slug": slug, "board_url": boardURL, "crawler_type": kind,
		"company_id": company, "metadata": metadata, "check_interval_minutes": check,
		"scrape_interval_hours": scrape, "throttle_key": throttle, "domain": throttle,
		"monitor_needs_browser": flag(monitorBrowser), "scraper_needs_browser": flag(scraperBrowser),
	}
	profile, err := InspectGreenhouseMonitor(boardID, canonical)
	if err != nil {
		return GreenhouseMonitorProfile{}, nil, err
	}
	cached, err := a.queue.redis.HGetAll(ctx, "board:"+boardID).Result()
	if err != nil {
		return GreenhouseMonitorProfile{}, nil, ErrObservation
	}
	projected, err := InspectGreenhouseMonitor(boardID, cached)
	if err != nil {
		return GreenhouseMonitorProfile{}, nil, err
	}
	if projected.EffectiveConfigSHA256 != profile.EffectiveConfigSHA256 {
		return GreenhouseMonitorProfile{}, nil, ErrAuthorityLost
	}
	return projected, cached, nil
}
