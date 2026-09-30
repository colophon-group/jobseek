package queue

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

// ObserveGreenhouseMonitor joins eligibility to an enabled, active canonical
// board and its current Redis projection under the existing epoch barrier. It
// never pops, heartbeats or publishes work. The detached observation is not an
// active ownership plan; selection and writes must validate authority afresh.
// Suspect/gone/quarantined and unsupported profiles retain their current owner.
func (a *Authority) ObserveGreenhouseMonitor(ctx context.Context, boardID string) (GreenhouseMonitorProfile, error) {
	if a == nil || a.queue == nil || a.pool == nil || !canonicalUUID.MatchString(boardID) {
		return GreenhouseMonitorProfile{}, ErrConfiguration
	}
	var result GreenhouseMonitorProfile
	err := a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		var slug, boardURL, kind, company, metadata, check, scrape, throttle string
		var monitorBrowser, scraperBrowser bool
		err := tx.QueryRow(ctx, `SELECT board_slug,board_url,crawler_type,company_id::text,
   COALESCE(metadata,'{}'::jsonb)::text,check_interval_minutes::text,
   scrape_interval_hours::text,COALESCE(throttle_key,''),
   monitor_needs_browser,scraper_needs_browser
   FROM public.job_board WHERE id=$1::uuid AND is_enabled AND board_status='active'
   FOR SHARE`, boardID).Scan(&slug, &boardURL, &kind, &company, &metadata, &check, &scrape, &throttle, &monitorBrowser, &scraperBrowser)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrUnsupportedProfile
		}
		if err != nil {
			return err
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
			return err
		}
		cached, err := a.queue.redis.HGetAll(ctx, "board:"+boardID).Result()
		if err != nil {
			return ErrObservation
		}
		projected, err := InspectGreenhouseMonitor(boardID, cached)
		if err != nil {
			return err
		}
		if projected.EffectiveConfigSHA256 != profile.EffectiveConfigSHA256 {
			return ErrAuthorityLost
		}
		result = projected
		return nil
	})
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return result, nil
}
