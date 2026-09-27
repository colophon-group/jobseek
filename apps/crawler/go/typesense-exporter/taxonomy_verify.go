package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

var taxonomySafeID = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

func taxonomyProjection(spec taxonomySpec, document map[string]any) (map[string]any, error) {
	projected := map[string]any{}
	unordered := map[string]bool{}
	for _, field := range spec.Unordered {
		unordered[field] = true
	}
	for _, field := range spec.Fields {
		value, exists := document[field]
		if !exists {
			continue
		}
		if unordered[field] {
			reflected := reflect.ValueOf(value)
			if !reflected.IsValid() || (reflected.Kind() != reflect.Slice && reflected.Kind() != reflect.Array) {
				return nil, errors.New("invalid taxonomy array field")
			}
			type item struct {
				value any
				key   string
			}
			items := make([]item, reflected.Len())
			for i := range items {
				v := reflected.Index(i).Interface()
				key, err := taxonomyCanonicalJSON(v, true)
				if err != nil {
					return nil, err
				}
				items[i] = item{v, string(key)}
			}
			sort.SliceStable(items, func(i, j int) bool { return items[i].key < items[j].key })
			normalized := make([]any, len(items))
			for i := range items {
				normalized[i] = items[i].value
			}
			value = normalized
		}
		projected[field] = value
	}
	id := ""
	if value, exists := projected["id"]; exists {
		id = fmt.Sprint(value)
	}
	if !taxonomySafeID.MatchString(id) {
		return nil, errors.New("unsafe taxonomy document id")
	}
	projected["id"] = id
	return projected, nil
}
func taxonomyHash(document map[string]any) ([32]byte, error) {
	data, err := taxonomyCanonicalJSON(document, false)
	if err != nil {
		return [32]byte{}, err
	}
	return sha256.Sum256(data), nil
}
func taxonomyDigest(hashes [][32]byte) string {
	sort.Slice(hashes, func(i, j int) bool { return bytes.Compare(hashes[i][:], hashes[j][:]) < 0 })
	hash := sha256.New()
	for _, item := range hashes {
		hash.Write(item[:])
	}
	return hex.EncodeToString(hash.Sum(nil))
}

// Python compares numeric values independently of their JSON spelling. Evidence
// hashes retain the original integer/float representation; comparison does not.
func taxonomyNumeric(value any) (*big.Rat, bool) {
	if number, ok := value.(json.Number); ok {
		if strings.ContainsAny(string(number), ".eE") {
			f, err := strconv.ParseFloat(string(number), 64)
			if err != nil {
				return nil, false
			}
			r := new(big.Rat).SetFloat64(f)
			return r, r != nil
		}
		r, ok := new(big.Rat).SetString(string(number))
		return r, ok
	}
	reflected := reflect.ValueOf(value)
	if !reflected.IsValid() {
		return nil, false
	}
	switch reflected.Kind() {
	case reflect.Bool:
		if reflected.Bool() {
			return big.NewRat(1, 1), true
		}
		return big.NewRat(0, 1), true
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return new(big.Rat).SetInt64(reflected.Int()), true
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return new(big.Rat).SetUint64(reflected.Uint()), true
	case reflect.Float32, reflect.Float64:
		r := new(big.Rat).SetFloat64(reflected.Float())
		return r, r != nil
	}
	return nil, false
}
func taxonomyEqual(left, right any) bool {
	if a, ok := taxonomyNumeric(left); ok {
		b, ok := taxonomyNumeric(right)
		return ok && a.Cmp(b) == 0
	}
	a, b := reflect.ValueOf(left), reflect.ValueOf(right)
	if !a.IsValid() || !b.IsValid() {
		return !a.IsValid() && !b.IsValid()
	}
	if (a.Kind() == reflect.Slice || a.Kind() == reflect.Array) && (b.Kind() == reflect.Slice || b.Kind() == reflect.Array) {
		if a.Len() != b.Len() {
			return false
		}
		for i := 0; i < a.Len(); i++ {
			if !taxonomyEqual(a.Index(i).Interface(), b.Index(i).Interface()) {
				return false
			}
		}
		return true
	}
	if a.Kind() == reflect.Map && b.Kind() == reflect.Map {
		if a.Len() != b.Len() {
			return false
		}
		for _, key := range a.MapKeys() {
			v := b.MapIndex(key)
			if !v.IsValid() || !taxonomyEqual(a.MapIndex(key).Interface(), v.Interface()) {
				return false
			}
		}
		return true
	}
	return reflect.DeepEqual(left, right)
}
func taxonomyStatus(ready bool) string {
	if ready {
		return "ready"
	}
	return "not_ready"
}
func taxonomySchemaEvidence(contract taxonomyContract, metadata map[string]map[string]any) (map[string]any, error) {
	requirements := map[string][]taxonomyField{"job_posting": contract.PostingSchema}
	for _, spec := range contract.Collections {
		requirements[spec.Collection] = spec.Schema
	}
	collections := map[string]any{}
	ready := true
	for collection, fields := range requirements {
		live, ok := metadata[collection]["fields"].([]any)
		if !ok {
			return nil, errors.New("invalid taxonomy schema metadata")
		}
		byName := map[string]map[string]any{}
		for _, value := range live {
			if field, ok := value.(map[string]any); ok {
				byName[fmt.Sprint(field["name"])] = field
			}
		}
		mismatches := []map[string]any{}
		for _, required := range fields {
			attributes := []string{}
			field, exists := byName[required.Name]
			if !exists {
				attributes = append(attributes, "missing")
			} else {
				if field["type"] != required.Type {
					attributes = append(attributes, "type")
				}
				index, exists := field["index"]
				if !exists {
					index = true
				}
				if required.Index != nil && index != *required.Index {
					attributes = append(attributes, "index")
				}
				facet, exists := field["facet"]
				if !exists {
					facet = false
				}
				if required.Facet != nil && facet != *required.Facet {
					attributes = append(attributes, "facet")
				}
			}
			if len(attributes) > 0 {
				mismatches = append(mismatches, map[string]any{"field": required.Name, "attributes": attributes})
			}
		}
		if len(mismatches) > 0 {
			ready = false
		}
		collections[collection] = map[string]any{"status": taxonomyStatus(len(mismatches) == 0), "mismatches": mismatches}
	}
	return map[string]any{"status": taxonomyStatus(ready), "collections": collections}, nil
}
func taxonomyCount(value any) (int, error) {
	number, ok := value.(json.Number)
	if !ok || strings.ContainsAny(string(number), ".eE") {
		return 0, errors.New("invalid taxonomy count")
	}
	count, err := strconv.Atoi(string(number))
	if err != nil || count < 0 {
		return 0, errors.New("invalid taxonomy count")
	}
	return count, nil
}

type taxonomyReader struct {
	Client  *http.Client
	BaseURL string
	Key     string
}

func (reader taxonomyReader) get(ctx context.Context, collection string, query url.Values) (map[string]any, error) {
	if reader.Client == nil || reader.Key == "" || !taxonomySafeID.MatchString(collection) {
		return nil, errors.New("invalid taxonomy client configuration")
	}
	endpoint, err := url.Parse(reader.BaseURL)
	if err != nil || (endpoint.Scheme != "http" && endpoint.Scheme != "https") || endpoint.Host == "" || endpoint.User != nil || endpoint.Path != "" || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return nil, errors.New("invalid Typesense origin")
	}
	endpoint.Path = "/collections/" + collection
	if query != nil {
		endpoint.Path += "/documents/search"
		endpoint.RawQuery = query.Encode()
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("X-TYPESENSE-API-KEY", reader.Key)
	response, err := reader.Client.Do(request)
	if err != nil {
		return nil, errors.New("Typesense taxonomy request failed")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Typesense taxonomy HTTP %d", response.StatusCode)
	}
	const limit = 32 << 20
	data, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil || len(data) > limit || !utf8.Valid(data) {
		return nil, errors.New("invalid or oversized taxonomy response")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var result map[string]any
	if err := decoder.Decode(&result); err != nil || result == nil {
		return nil, errors.New("invalid taxonomy response")
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return nil, errors.New("trailing taxonomy response data")
	}
	return result, nil
}

type taxonomySearch func(context.Context, string, url.Values) (map[string]any, error)

func compareTaxonomyCollection(ctx context.Context, search taxonomySearch, spec taxonomySpec, documents []map[string]any, metadata map[string]any, pageSize int) (map[string]any, int, error) {
	if pageSize < 1 || pageSize > 250 {
		return nil, 0, errors.New("invalid taxonomy page size")
	}
	count, err := taxonomyCount(metadata["num_documents"])
	if err != nil {
		return nil, 0, err
	}
	expected := map[string]map[string]any{}
	expectedHashes := [][32]byte{}
	for _, document := range documents {
		projection, err := taxonomyProjection(spec, document)
		if err != nil {
			return nil, 0, err
		}
		id := projection["id"].(string)
		if _, exists := expected[id]; exists {
			return nil, 0, errors.New("duplicate authoritative taxonomy id")
		}
		expected[id] = projection
		hash, err := taxonomyHash(projection)
		if err != nil {
			return nil, 0, err
		}
		expectedHashes = append(expectedHashes, hash)
	}
	seen := map[string]bool{}
	actualHashes := [][32]byte{}
	details := []map[string]any{}
	mismatchCount := 0
	found := -1
	calls := 0
	mismatch := func(kind, id string, fields []string) {
		mismatchCount++
		if len(details) >= 20 {
			return
		}
		key := sha256.Sum256([]byte(id))
		detail := map[string]any{"kind": kind, "document_key_sha256": hex.EncodeToString(key[:])}
		if fields != nil {
			detail["fields"] = fields
		}
		details = append(details, detail)
	}
	for page := 1; ; page++ {
		response, err := search(ctx, spec.Collection, url.Values{"q": {"*"}, "query_by": {spec.QueryBy}, "include_fields": {strings.Join(spec.Fields, ",")}, "page": {strconv.Itoa(page)}, "per_page": {strconv.Itoa(pageSize)}})
		calls++
		if err != nil {
			return nil, calls, err
		}
		responseFound, err := taxonomyCount(response["found"])
		if err != nil {
			return nil, calls, err
		}
		hits, ok := response["hits"].([]any)
		if !ok {
			return nil, calls, errors.New("invalid taxonomy hits")
		}
		if found < 0 {
			found = responseFound
		} else if found != responseFound {
			return nil, calls, errors.New("taxonomy count changed during verification")
		}
		previousSeen := len(seen)
		for _, raw := range hits {
			hit, ok := raw.(map[string]any)
			if !ok {
				return nil, calls, errors.New("invalid taxonomy hit")
			}
			doc, ok := hit["document"].(map[string]any)
			if !ok {
				return nil, calls, errors.New("invalid taxonomy document")
			}
			projection, err := taxonomyProjection(spec, doc)
			if err != nil {
				return nil, calls, err
			}
			id := projection["id"].(string)
			hash, err := taxonomyHash(projection)
			if err != nil {
				return nil, calls, err
			}
			actualHashes = append(actualHashes, hash)
			if seen[id] {
				mismatch("duplicate", id, nil)
				continue
			}
			seen[id] = true
			wanted, exists := expected[id]
			if !exists {
				mismatch("unexpected", id, nil)
				continue
			}
			fields := []string{}
			for _, field := range spec.Fields {
				a, aExists := projection[field]
				b, bExists := wanted[field]
				if aExists != bExists || !taxonomyEqual(a, b) {
					fields = append(fields, field)
				}
			}
			if len(fields) > 0 {
				mismatch("field_mismatch", id, fields)
			}
		}
		if len(seen) >= found {
			break
		}
		if len(hits) == 0 {
			return nil, calls, errors.New("taxonomy pagination ended before count")
		}
		// A repeating server page must not turn a readiness check into an endless loop.
		if len(seen) == previousSeen {
			return nil, calls, errors.New("taxonomy pagination made no progress")
		}
	}
	missing := []string{}
	for id := range expected {
		if !seen[id] {
			missing = append(missing, id)
		}
	}
	sort.Strings(missing)
	for _, id := range missing {
		mismatch("missing", id, nil)
	}
	countMatches := count == len(documents) && found == len(documents)
	minimum := len(documents) >= spec.Minimum
	return map[string]any{
		"status": taxonomyStatus(countMatches && minimum && mismatchCount == 0), "authoritative_document_count": len(documents), "typesense_document_count": count, "typesense_search_found": found,
		"count_matches": countMatches, "minimum_document_count": spec.Minimum, "minimum_satisfied": minimum, "compared_document_count": len(seen), "compared_fields": spec.Fields,
		"expected_projection_sha256": taxonomyDigest(expectedHashes), "typesense_projection_sha256": taxonomyDigest(actualHashes), "mismatch_count": mismatchCount, "mismatch_details": details, "mismatch_details_truncated": mismatchCount > len(details),
	}, calls, nil
}

func verifyTaxonomySnapshot(ctx context.Context, reader taxonomyReader, contract taxonomyContract, documents taxonomyDocuments, pageSize int) (map[string]any, error) {
	if pageSize < 1 || pageSize > 250 {
		return nil, errors.New("invalid taxonomy page size")
	}
	names := []string{}
	metadata := map[string]map[string]any{}
	for _, spec := range contract.Collections {
		names = append(names, spec.Collection)
	}
	for _, name := range append([]string{"job_posting"}, names...) {
		value, err := reader.get(ctx, name, nil)
		if err != nil {
			return nil, err
		}
		metadata[name] = value
	}
	schema, err := taxonomySchemaEvidence(contract, metadata)
	if err != nil {
		return nil, err
	}
	comparisons := map[string]any{}
	calls := 0
	ready := schema["status"] == "ready"
	for _, spec := range contract.Collections {
		comparison, count, err := compareTaxonomyCollection(ctx, reader.get, spec, documents[spec.Collection], metadata[spec.Collection], pageSize)
		if err != nil {
			return nil, err
		}
		calls += count
		comparisons[spec.Collection] = comparison
		ready = ready && comparison["status"] == "ready"
	}
	return map[string]any{
		"command": "verify-typesense-taxonomies", "status": taxonomyStatus(ready), "authority": "local_postgres",
		"coverage":        map[string]any{"document_counts": "exact", "documents": "full", "fields": "static_consumer_contract", "collections": names, "excluded_dynamic_fields": []string{"active_posting_count", "has_active_postings"}},
		"snapshot":        map[string]any{"isolation": "repeatable_read", "read_only": true},
		"pagination":      map[string]any{"maximum_documents_per_search": 250, "configured_documents_per_search": pageSize, "retained_remote_state": "document_ids_and_sha256_only"},
		"typesense_calls": map[string]any{"collection_metadata": len(metadata), "document_searches": calls, "total": len(metadata) + calls}, "schema": schema, "collections": comparisons,
	}, nil
}
