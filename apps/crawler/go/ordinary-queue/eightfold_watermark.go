package queue

import (
	"bytes"
	"encoding/json"
	"net/url"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func validateEightfoldWatermarkUpdate(config map[string]string, current, patch map[string]any) error {
	if config["crawler_type"] != "eightfold" || len(patch) != 1 || patch["pcsx_watermark"] == nil {
		return ErrConfiguration
	}
	body, e := json.Marshal(patch["pcsx_watermark"])
	if e != nil || len(body) > 16384 {
		return ErrConfiguration
	}
	fields, e := profileMetadataFields(string(body), map[string]bool{"max_ts": true, "last_full_at": true, "last_incremental_at": true, "enabled": true, "extra": true, "interval_days": true, "auto_full_crawl": true})
	if e != nil || fields["max_ts"] == nil || fields["interval_days"] == nil || fields["auto_full_crawl"] == nil || fields["enabled"] == nil || fields["extra"] == nil {
		return ErrConfiguration
	}
	previousRaw, e := json.Marshal(current["pcsx_watermark"])
	if e != nil {
		return ErrConfiguration
	}
	before, e := stableEightfoldWatermark(previousRaw)
	if e != nil {
		return e
	}
	after, e := stableEightfoldWatermark(body)
	if e != nil || !bytes.Equal(before, after) {
		return ErrAuthorityLost
	}
	d, e := api.Decode(body)
	if e != nil {
		return ErrConfiguration
	}
	value, ok := d.Value.(map[string]any)
	if !ok {
		return ErrConfiguration
	}
	next, e := api.EightfoldReadWatermark(map[string]any{"pcsx_watermark": value})
	if e != nil || next.Enabled == nil {
		return ErrConfiguration
	}
	previous, e := api.EightfoldReadWatermark(current)
	if e != nil {
		return ErrConfiguration
	}
	if next.MaxTS < previous.MaxTS {
		return ErrAuthorityLost
	}
	for key, parsed := range map[string]bool{"last_full_at": next.LastFullAt != nil, "last_incremental_at": next.LastIncrementalAt != nil} {
		if _, present := fields[key]; present && !parsed {
			return ErrConfiguration
		}
	}
	if previous.LastFullAt != nil && (next.LastFullAt == nil || next.LastFullAt.Before(*previous.LastFullAt)) {
		return ErrAuthorityLost
	}
	if previous.LastIncrementalAt != nil && (next.LastIncrementalAt == nil || next.LastIncrementalAt.Before(*previous.LastIncrementalAt)) {
		return ErrAuthorityLost
	}
	origin, e := url.Parse(config["board_url"])
	if e != nil {
		return ErrConfiguration
	}
	host, _ := next.Extra["host"].(string)
	domain, _ := next.Extra["domain"].(string)
	if host != origin.Hostname() || domain == "" || len(domain) > 256 {
		return ErrConfiguration
	}
	return nil
}

// PostgreSQL owns the watermark. After its terminal commit (or while cold),
// replace only that subkey in the still exact cached metadata. No immutable
// field is projected from a detached fetch or an arbitrary patch.
func eightfoldCacheMetadata(config map[string]string, canonical json.RawMessage) (*string, error) {
	if config["crawler_type"] != "eightfold" {
		return nil, ErrConfiguration
	}
	md, e := profileMetadataFields(config["metadata"], nil)
	if e != nil {
		return nil, e
	}
	before, e := stableEightfoldWatermark(md["pcsx_watermark"])
	if e != nil {
		return nil, e
	}
	after, e := stableEightfoldWatermark(canonical)
	if e != nil || !bytes.Equal(before, after) {
		return nil, ErrAuthorityLost
	}
	if len(canonical) == 0 || string(canonical) == "null" {
		delete(md, "pcsx_watermark")
	} else {
		md["pcsx_watermark"] = canonical
	}
	body, e := json.Marshal(md)
	if e != nil || len(body) > 1<<20 {
		return nil, ErrConfiguration
	}
	value := string(body)
	if value == config["metadata"] {
		return nil, nil
	}
	return &value, nil
}
