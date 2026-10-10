package queue

import (
	"context"
	_ "embed"
	"encoding/json"
	"math"
	"regexp"
	"sort"

	"github.com/jackc/pgx/v5"
)

//go:embed postfinance_identity_migration.sql
var postfinanceIdentityMigrationSQL string

const postfinanceIdentityMigration = "postfinance-swiss-post-stable-id-v1"
const postfinanceMigrationFingerprint = "94dd0b3abd85e29028d1819a805ce204c8eefcdb6d8695881bf6834168e994f1"
const postfinanceMigrationBoardURL = "https://jobs.postfinance.ch/search/?locale=de_DE"
const postfinanceCanonicalPattern = `^https://jobs[.]postfinance[.]ch/job/_/[0-9]+/$`
const postfinanceLegacyPattern = `^(https://job[.]post[.]ch/([A-Za-z][A-Za-z0-9]*/)?job/[^/?#]+/[0-9]+-[a-z]{2}_[A-Z]{2}|https://job[.]post[.]ch/search[?][^#]+(#[^#]*)?|https://career[.]post[.]ch/(de|en)|https://www[.]post[.]ch/en/pages/footer/privacy-policy-for-job-applicants)$`

var postfinanceCanonicalURL = regexp.MustCompile(postfinanceCanonicalPattern)

func postfinanceMigrationRequested(md map[string]json.RawMessage) bool {
	var marker string
	return json.Unmarshal(md["identity_migration"], &marker) == nil && marker == postfinanceIdentityMigration
}

func validPostfinanceMigrationReceipt(raw json.RawMessage) bool {
	fields, err := profileMetadataFields(string(raw), map[string]bool{"id": true, "version": true, "config_fingerprint": true, "completed_at": true, "retired_count": true})
	if err != nil || len(fields) != 5 {
		return false
	}
	var id, fingerprint, completed string
	var version any
	var retired int
	if json.Unmarshal(fields["version"], &version) != nil {
		return false
	}
	versionMatches := version == float64(1) || version == true
	return json.Unmarshal(fields["id"], &id) == nil && id == postfinanceIdentityMigration &&
		versionMatches &&
		json.Unmarshal(fields["config_fingerprint"], &fingerprint) == nil && fingerprint == postfinanceMigrationFingerprint &&
		json.Unmarshal(fields["completed_at"], &completed) == nil && completed != "" &&
		string(fields["retired_count"]) != "null" && json.Unmarshal(fields["retired_count"], &retired) == nil && retired >= 0 && retired <= 2000
}

// Only this code-owned replacement board can request company-wide retirement.
// Other retained receipts remain inert, immutable configuration as before.
func postfinanceMigrationConfig(config map[string]string, md map[string]json.RawMessage) error {
	raw := md["identity_migration"]
	if len(raw) == 0 || string(raw) == "null" {
		return nil
	}
	if !postfinanceMigrationRequested(md) || config["crawler_type"] != "rss" || config["board_slug"] != "postfinance-careers" || config["board_url"] != postfinanceMigrationBoardURL {
		return ErrUnsupportedProfile
	}
	if receipt := md["_identity_migration_receipt"]; len(receipt) > 0 && string(receipt) != "null" && !validPostfinanceMigrationReceipt(receipt) {
		return ErrUnsupportedProfile
	}
	return nil
}

// Run inside ordinary terminal success, after canonical writes and before gone
// guards. The original SQL locks the board and company-scoped active rows,
// verifies every discovered URL was touched this cycle, and retires strict
// legacy rows together with the durable receipt. Failed eligibility is a no-op.
func (c *GreenhouseCycle) migratePostfinanceIdentities(ctx context.Context, tx pgx.Tx, md map[string]any, inventory GreenhouseInventorySummary) (int, error) {
	config := c.claim.task.Config
	if config["crawler_type"] != "rss" || config["board_slug"] != "postfinance-careers" || config["board_url"] != postfinanceMigrationBoardURL || md["identity_migration"] != postfinanceIdentityMigration || md["_monitor_config_fingerprint"] != postfinanceMigrationFingerprint {
		return 0, nil
	}
	if md["_identity_migration_receipt"] != nil {
		return 0, nil
	}
	if inventory.Truncated || inventory.ProcessingFiltered != 0 || inventory.Discovered <= 0 || inventory.Discovered != c.processed || c.processed != len(c.identities) || len(c.identities) > 2000 {
		return 0, nil
	}
	urls := make([]string, 0, len(c.identities))
	for url := range c.identities {
		if !postfinanceCanonicalURL.MatchString(url) {
			return 0, nil
		}
		urls = append(urls, url)
	}
	history, ok := md["recent_discovered_counts"].([]any)
	if !ok || len(history) < 3 {
		return 0, nil
	}
	values := make([]float64, len(history))
	for i, raw := range history {
		n, ok := raw.(json.Number)
		if !ok {
			return 0, nil
		}
		value, err := n.Float64()
		if err != nil || math.IsInf(value, 0) || math.IsNaN(value) {
			return 0, nil
		}
		values[i] = value
	}
	sort.Float64s(values)
	median := values[len(values)/2]
	if len(values)%2 == 0 {
		median = (values[len(values)/2-1] + median) / 2
	}
	drop, err := workdayLifecycleSetting(md["drop_threshold"], 0.3)
	if err != nil {
		return 0, err
	}
	if median <= 0 || float64(inventory.Discovered) < median*(1-drop) {
		return 0, nil
	}
	sort.Strings(urls)
	baseReceipt, err := json.Marshal(map[string]any{"id": postfinanceIdentityMigration, "version": 1, "config_fingerprint": postfinanceMigrationFingerprint})
	if err != nil {
		return 0, err
	}
	var active, legacy, canonical, unknown, discovered, validated, retired int
	var written bool
	var receipt json.RawMessage
	err = tx.QueryRow(ctx, postfinanceIdentityMigrationSQL, c.claim.task.ID, config["company_id"], c.startedAt, 2000, urls, postfinanceLegacyPattern, postfinanceCanonicalPattern, string(baseReceipt), "postfinance", true).Scan(&active, &legacy, &canonical, &unknown, &discovered, &validated, &retired, &written, &receipt)
	if err != nil {
		return 0, err
	}
	if len(receipt) > 0 && string(receipt) != "null" || !written {
		return 0, nil
	}
	if unknown != 0 || discovered != len(urls) || validated != discovered || retired != legacy {
		return 0, ErrConfiguration
	}
	return retired, nil
}
