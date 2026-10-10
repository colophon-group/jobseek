package queue

import (
	"context"
	_ "embed"
	"encoding/json"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5"
)

// The exact original Python statement adopts one legacy alias in place and
// atomically retires the remaining aliases under its existing bounded gate.
//
//go:embed unisante_identity_migration.sql
var unisanteIdentityMigrationSQL string

const unisanteIdentityMigration = "unisante-provider-reference-v1"

var unisanteIdentity = regexp.MustCompile(`^unisante:emploi:([1-9][0-9]{0,8})$`)
var unisanteOfficialDetail = regexp.MustCompile(`^https://emploi[.]unisante[.]ch/index[.]php/offre/[a-z0-9]+(-[a-z0-9]+)*/?$`)

func unisanteMigrationRequested(md map[string]json.RawMessage) bool {
	var value string
	return json.Unmarshal(md["identity_migration"], &value) == nil && value == unisanteIdentityMigration
}

func validUnisanteMigrationReceipt(raw json.RawMessage) bool {
	fields, err := profileMetadataFields(string(raw), map[string]bool{"id": true, "version": true, "completed_at": true, "updated_count": true, "retired_count": true})
	if err != nil || len(fields) != 5 {
		return false
	}
	var id, completed string
	var version any
	var updated, retired int
	if json.Unmarshal(fields["version"], &version) != nil {
		return false
	}
	versionMatches := false
	switch value := version.(type) {
	case float64:
		versionMatches = value == 1
	case bool:
		versionMatches = value
	}
	return json.Unmarshal(fields["id"], &id) == nil && id == unisanteIdentityMigration &&
		versionMatches &&
		json.Unmarshal(fields["completed_at"], &completed) == nil && completed != "" &&
		string(fields["updated_count"]) != "null" && json.Unmarshal(fields["updated_count"], &updated) == nil && updated >= 0 && updated <= 50 &&
		string(fields["retired_count"]) != "null" && json.Unmarshal(fields["retired_count"], &retired) == nil && retired >= 0 && retired <= 50
}

func unisanteMigrationConfig(config map[string]string) (map[string]json.RawMessage, error) {
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return nil, ErrConfiguration
	}
	var migration string
	if raw := md["identity_migration"]; len(raw) > 0 && string(raw) != "null" {
		if json.Unmarshal(raw, &migration) != nil || migration != unisanteIdentityMigration {
			return nil, ErrConfiguration
		}
	}
	if migration != "" && (config["crawler_type"] != "unisante" || config["board_slug"] != "unisante-emploi" || config["board_url"] != "https://emploi.unisante.ch/index.php/offres") {
		return nil, ErrConfiguration
	}
	if raw := md["_identity_migration_receipt"]; len(raw) > 0 && string(raw) != "null" && !validUnisanteMigrationReceipt(raw) {
		return nil, ErrConfiguration
	}
	return md, nil
}

// This transaction-local helper grants no claim or writer authority. The full
// inventory caller must prove unfiltered completeness before invoking it inside
// the existing board-locked, claim-fenced write transaction.
func migrateUnisanteProviderIdentities(ctx context.Context, tx pgx.Tx, boardID, companyID string, config map[string]string, identities, urls []string) (int, error) {
	md, err := unisanteMigrationConfig(config)
	if err != nil {
		return 0, err
	}
	var migration string
	json.Unmarshal(md["identity_migration"], &migration)
	if migration != "unisante-provider-reference-v1" {
		return 0, nil
	}
	if receipt := md["_identity_migration_receipt"]; len(receipt) > 0 && string(receipt) != "null" {
		return 0, nil
	}
	if len(identities) == 0 || len(identities) > 50 || len(identities) != len(urls) {
		return 0, ErrConfiguration
	}
	seenIdentities, seenURLs := map[string]bool{}, map[string]bool{}
	for i, id := range identities {
		if !unisanteIdentity.MatchString(id) || !unisanteOfficialDetail.MatchString(urls[i]) || seenIdentities[id] || seenURLs[urls[i]] || strings.ContainsRune(urls[i], 0) {
			return 0, ErrConfiguration
		}
		seenIdentities[id], seenURLs[urls[i]] = true, true
	}
	var receipt json.RawMessage
	var active, legacy, canonical, unknown, discovered, valid, conflicts, candidates, existingCanonicals, updated, retired int
	var mayMigrate, receiptWritten bool
	err = tx.QueryRow(ctx, unisanteIdentityMigrationSQL, boardID, companyID, identities, urls, 50, `{"id":"unisante-provider-reference-v1","version":1}`).Scan(&receipt, &active, &legacy, &canonical, &unknown, &discovered, &valid, &conflicts, &candidates, &existingCanonicals, &updated, &retired, &mayMigrate, &receiptWritten)
	if err != nil {
		return 0, err
	}
	if len(receipt) > 0 && string(receipt) != "null" {
		if !validUnisanteMigrationReceipt(receipt) {
			return 0, ErrConfiguration
		}
		return 0, nil
	}
	if !mayMigrate || !receiptWritten || discovered != len(identities) || valid != discovered || conflicts != 0 || unknown != 0 || updated+retired != legacy {
		return 0, ErrConfiguration
	}
	return retired, nil
}
