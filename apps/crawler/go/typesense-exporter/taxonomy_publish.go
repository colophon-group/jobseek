package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strconv"
)

// Company pruning is allowed only after every upsert is acknowledged and an
// exact census contains every authoritative ID. The final census must converge.
func fetchCompanyIDs(ctx context.Context, reader taxonomyReader) (map[string]bool, error) {
	metadata, err := reader.get(ctx, "company", nil)
	if err != nil {
		return nil, err
	}
	count, err := taxonomyCount(metadata["num_documents"])
	if err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	returned := 0
	for page := 1; ; page++ {
		response, err := reader.get(ctx, "company", url.Values{"q": {"*"}, "query_by": {"name"}, "include_fields": {"id"}, "enable_overrides": {"false"}, "page": {strconv.Itoa(page)}, "per_page": {"250"}})
		if err != nil {
			return nil, err
		}
		found, err := taxonomyCount(response["found"])
		if err != nil || found != count {
			return nil, errors.New("company metadata/search count mismatch")
		}
		hits, ok := response["hits"].([]any)
		if !ok || len(hits) != min(250, count-returned) {
			return nil, errors.New("incomplete company census page")
		}
		for _, raw := range hits {
			hit, ok := raw.(map[string]any)
			if !ok {
				return nil, errors.New("invalid company census hit")
			}
			doc, ok := hit["document"].(map[string]any)
			if !ok {
				return nil, errors.New("invalid company census document")
			}
			id, ok := doc["id"].(string)
			if !ok || id == "" || seen[id] {
				return nil, errors.New("invalid or repeated company census ID")
			}
			seen[id] = true
		}
		returned += len(hits)
		if returned == count {
			break
		}
	}
	if len(seen) != count {
		return nil, errors.New("company census did not converge")
	}
	return seen, nil
}
func deleteCompanyID(ctx context.Context, reader taxonomyReader, id string) error {
	endpoint, err := url.Parse(reader.BaseURL)
	if err != nil {
		return errors.New("invalid company deletion origin")
	}
	endpoint.Path = "/collections/company/documents/" + id
	endpoint.RawPath = "/collections/company/documents/" + url.PathEscape(id)
	request, err := http.NewRequestWithContext(ctx, http.MethodDelete, endpoint.String(), nil)
	if err != nil {
		return errors.New("invalid company deletion request")
	}
	request.Header.Set("X-TYPESENSE-API-KEY", reader.Key)
	response, err := reader.Client.Do(request)
	if err != nil {
		return errors.New("company deletion failed")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("company deletion HTTP %d", response.StatusCode)
	}
	return nil
}

type taxonomyPublisher struct {
	Import        func(context.Context, string, []map[string]any) error
	CompanyIDs    func(context.Context) (map[string]bool, error)
	DeleteCompany func(context.Context, string) error
}

func newTaxonomyPublisher(reader taxonomyReader) taxonomyPublisher {
	return taxonomyPublisher{
		Import: func(ctx context.Context, collection string, docs []map[string]any) error {
			failed, err := importCollectionDocs(ctx, reader.Client, reader.BaseURL, reader.Key, collection, "upsert", docs)
			if err != nil || len(failed) > 0 {
				return errors.New("taxonomy import was not fully acknowledged")
			}
			return nil
		},
		CompanyIDs:    func(ctx context.Context) (map[string]bool, error) { return fetchCompanyIDs(ctx, reader) },
		DeleteCompany: func(ctx context.Context, id string) error { return deleteCompanyID(ctx, reader, id) },
	}
}
func (publisher taxonomyPublisher) upsert(ctx context.Context, collection string, docs []map[string]any) error {
	for offset := 0; offset < len(docs); offset += 1000 {
		if err := publisher.Import(ctx, collection, docs[offset:min(offset+1000, len(docs))]); err != nil {
			return err
		}
	}
	return nil
}
func (publisher taxonomyPublisher) companies(ctx context.Context, docs []map[string]any) (int, error) {
	if len(docs) == 0 {
		return 0, errors.New("company authority is empty")
	}
	expected := map[string]bool{}
	for _, doc := range docs {
		id, ok := doc["id"].(string)
		if !ok || id == "" || expected[id] {
			return 0, errors.New("invalid company authority")
		}
		expected[id] = true
	}
	if err := publisher.upsert(ctx, "company", docs); err != nil {
		return 0, err
	}
	remote, err := publisher.CompanyIDs(ctx)
	if err != nil {
		return 0, err
	}
	for id := range expected {
		if !remote[id] {
			return 0, errors.New("company prune blocked by missing authoritative IDs")
		}
	}
	stale := []string{}
	for id := range remote {
		if !expected[id] {
			stale = append(stale, id)
		}
	}
	sort.Strings(stale)
	if len(stale) > 50 || len(stale)*10000 > len(remote)*100 {
		return 0, errors.New("company prune exceeds count or percentage budget")
	}
	deleted := 0
	for _, id := range stale {
		if err := publisher.DeleteCompany(ctx, id); err != nil {
			return deleted, err
		}
		deleted++
	}
	converged, err := publisher.CompanyIDs(ctx)
	if err != nil {
		return deleted, err
	}
	if len(converged) != len(expected) {
		return deleted, errors.New("company index did not converge")
	}
	for id := range expected {
		if !converged[id] {
			return deleted, errors.New("company index did not converge")
		}
	}
	return deleted, nil
}
