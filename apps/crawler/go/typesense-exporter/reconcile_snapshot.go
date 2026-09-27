package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
)

const reconciliationPartitions = 256

// Match the Python reconciler's user-visible payload contract. Location and
// technology arrays are positional; only locales and occupation ancestors are
// sets. Fingerprints are attempt-local, never persisted or trusted from remote
// documents.
var reconciliationPayloadFields = []string{
	"company_id", "company_name", "company_slug", "company_icon", "title", "has_content",
	"location_ids", "location_direct_ids", "location_names", "location_types", "location_geo_types",
	"occupation_id", "occupation_ids", "occupation_name", "seniority_id", "seniority_name",
	"technology_ids", "technology_names", "employment_type", "salary_eur", "salary_min",
	"salary_max", "salary_currency", "salary_period", "experience_min", "experience_max",
	"experience_min_years", "experience_max_years", "locales", "first_seen_at",
	"candidate_order_key", "candidate_order_hi", "candidate_order_lo", "source_url",
}

type reconciliationDocument struct {
	Active      bool
	Fingerprint [32]byte
}

type reconciliationSnapshot map[string]reconciliationDocument
type reconciliationIDs map[string]struct{}

type reconciliationDiff struct {
	Missing, State, Payload, RemoteActive, RemoteInactive reconciliationIDs
}

func (d reconciliationDiff) candidates() reconciliationIDs {
	result := reconciliationIDs{}
	for _, group := range []reconciliationIDs{d.Missing, d.State, d.Payload, d.RemoteActive, d.RemoteInactive} {
		for id := range group {
			result[id] = struct{}{}
		}
	}
	return result
}

func compareReconciliationSnapshots(local, remote reconciliationSnapshot) reconciliationDiff {
	diff := reconciliationDiff{reconciliationIDs{}, reconciliationIDs{}, reconciliationIDs{}, reconciliationIDs{}, reconciliationIDs{}}
	for id, expected := range local {
		actual, found := remote[id]
		if !found {
			diff.Missing[id] = struct{}{}
			continue
		}
		if expected.Active != actual.Active {
			diff.State[id] = struct{}{}
		}
		if expected.Fingerprint != actual.Fingerprint {
			diff.Payload[id] = struct{}{}
		}
	}
	for id, actual := range remote {
		if _, found := local[id]; !found {
			if actual.Active {
				diff.RemoteActive[id] = struct{}{}
			} else {
				diff.RemoteInactive[id] = struct{}{}
			}
		}
	}
	return diff
}

func (s reconciliationSnapshot) subset(ids reconciliationIDs) reconciliationSnapshot {
	result := reconciliationSnapshot{}
	for id := range ids {
		if document, found := s[id]; found {
			result[id] = document
		}
	}
	return result
}

func (s reconciliationSnapshot) active() int {
	count := 0
	for _, document := range s {
		if document.Active {
			count++
		}
	}
	return count
}

func orderedReconciliationIDs(ids reconciliationIDs) []string {
	result := make([]string, 0, len(ids))
	for id := range ids {
		result = append(result, id)
	}
	sort.Strings(result)
	return result
}

func reconciliationBounds(partition int) (string, *string, error) {
	if partition < 0 || partition >= reconciliationPartitions {
		return "", nil, errors.New("reconciliation partition must be 0..255")
	}
	lower := fmt.Sprintf("%02x000000-0000-0000-0000-000000000000", partition)
	if partition == 255 {
		return lower, nil, nil
	}
	upper := fmt.Sprintf("%02x000000-0000-0000-0000-000000000000", partition+1)
	return lower, &upper, nil
}

// Accept the UUID forms accepted by Python uuid.UUID, then key the comparison
// by the canonical value. This also detects alternate-spelling duplicates.
func reconciliationUUID(raw string) (string, error) {
	raw = strings.ReplaceAll(strings.ReplaceAll(raw, "urn:", ""), "uuid:", "")
	raw = strings.Trim(raw, "{}")
	raw = strings.ReplaceAll(raw, "-", "")
	if len(raw) != 32 {
		return "", errors.New("invalid reconciliation UUID")
	}
	decoded, err := hex.DecodeString(raw)
	if err != nil {
		return "", errors.New("invalid reconciliation UUID")
	}
	value := hex.EncodeToString(decoded)
	return value[:8] + "-" + value[8:12] + "-" + value[12:16] + "-" + value[16:20] + "-" + value[20:], nil
}

// Canonicalize numeric values without converting signed int64 candidate-order
// words through float64. A typed encoding makes 1 and 1.0 equivalent without
// conflating either with the string "1" or a user-supplied object.
func canonicalReconciliationValue(value any) (string, error) {
	switch value := value.(type) {
	case nil:
		return "null", nil
	case bool, string:
		raw, err := json.Marshal(value)
		return string(raw), err
	case json.Number:
		if len(value) > 4300 {
			return "", errors.New("reconciliation number exceeds safety limit")
		}
		if !strings.ContainsAny(string(value), ".eE") {
			number, ok := new(big.Int).SetString(string(value), 10)
			if !ok {
				return "", errors.New("invalid reconciliation integer")
			}
			return "number(" + number.String() + ")", nil
		}
		// Python's JSON decoder retains integer words exactly and decodes
		// decimal/exponent values as IEEE doubles. Preserve that distinction.
		number, err := strconv.ParseFloat(string(value), 64)
		if err != nil || math.IsInf(number, 0) || math.IsNaN(number) {
			return "", errors.New("invalid reconciliation float")
		}
		if math.Trunc(number) == number {
			integer, _ := new(big.Float).SetFloat64(number).Int(nil)
			return "number(" + integer.String() + ")", nil
		}
		return "number(" + strconv.FormatFloat(number, 'g', -1, 64) + ")", nil
	case []any:
		parts := make([]string, len(value))
		for i, item := range value {
			part, err := canonicalReconciliationValue(item)
			if err != nil {
				return "", err
			}
			parts[i] = part
		}
		return "[" + strings.Join(parts, ",") + "]", nil
	case map[string]any:
		keys := make([]string, 0, len(value))
		for key := range value {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		parts := make([]string, 0, len(keys))
		for _, key := range keys {
			part, err := canonicalReconciliationValue(value[key])
			if err != nil {
				return "", err
			}
			encodedKey, _ := json.Marshal(key)
			parts = append(parts, string(encodedKey)+":"+part)
		}
		return "{" + strings.Join(parts, ",") + "}", nil
	default:
		return "", errors.New("unsupported reconciliation payload value")
	}
}

func reconciliationFingerprint(document map[string]any) ([32]byte, error) {
	// Local projections use native Go numeric/slice types. Decode through the
	// same lossless JSON path as remote exports before comparing either side.
	raw, err := json.Marshal(document)
	if err != nil {
		return [32]byte{}, err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var normalized map[string]any
	if err := decoder.Decode(&normalized); err != nil {
		return [32]byte{}, err
	}
	payload := make(map[string]any, len(reconciliationPayloadFields))
	for _, field := range reconciliationPayloadFields {
		value := normalized[field]
		if field == "locales" || field == "occupation_ids" {
			if array, ok := value.([]any); ok {
				keys := make([]struct {
					key   string
					value any
				}, len(array))
				for i, item := range array {
					key, err := canonicalReconciliationValue(item)
					if err != nil {
						return [32]byte{}, err
					}
					keys[i].key, keys[i].value = key, item
				}
				sort.Slice(keys, func(i, j int) bool { return keys[i].key < keys[j].key })
				for i, item := range keys {
					array[i] = item.value
				}
			}
		}
		payload[field] = value
	}
	canonical, err := canonicalReconciliationValue(payload)
	if err != nil {
		return [32]byte{}, err
	}
	return sha256.Sum256([]byte(canonical)), nil
}

func addReconciliationDocument(snapshot reconciliationSnapshot, document map[string]any) error {
	rawID, ok := document["id"].(string)
	if !ok {
		return errors.New("reconciliation document has invalid ID")
	}
	id, err := reconciliationUUID(rawID)
	if err != nil {
		return err
	}
	active, ok := document["is_active"].(bool)
	if !ok {
		return errors.New("reconciliation document has invalid active state")
	}
	if _, exists := snapshot[id]; exists {
		return errors.New("duplicate reconciliation document")
	}
	fingerprint, err := reconciliationFingerprint(document)
	if err != nil {
		return err
	}
	snapshot[id] = reconciliationDocument{Active: active, Fingerprint: fingerprint}
	return nil
}
