package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

const maxImportResponseBytes = 64 << 20

type importFailure struct {
	Reason string
	Code   int
}

// importDocs performs one idempotent Typesense upsert. Every submitted row
// must receive an explicit boolean acknowledgement in the same position.
// A transport, HTTP, cardinality, or decode error leaves the caller's CDC
// cursor pinned; individual rejected documents are returned for the current
// exporter's logged poison-row semantics. The caller owns cross-tick backoff.
func importDocs(ctx context.Context, client *http.Client, baseURL, apiKey string, docs []map[string]any) (map[string]importFailure, error) {
	return importCollectionDocs(ctx, client, baseURL, apiKey, "job_posting", "upsert", docs)
}

func importCollectionDocs(ctx context.Context, client *http.Client, baseURL, apiKey, collection, action string, docs []map[string]any) (map[string]importFailure, error) {
	if client == nil || apiKey == "" {
		return nil, errors.New("Typesense client and operations key are required")
	}
	if collection != "job_posting" && collection != "location" && collection != "occupation" && collection != "seniority" && collection != "technology" && collection != "company" {
		return nil, errors.New("unsupported Typesense collection")
	}
	if action != "upsert" && action != "update" {
		return nil, errors.New("unsupported Typesense import action")
	}
	if len(docs) == 0 {
		return nil, errors.New("Typesense import requires at least one document")
	}
	u, err := url.Parse(baseURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		return nil, errors.New("Typesense base URL must be an HTTP(S) origin")
	}
	u.Path = "/collections/" + collection + "/documents/import"
	u.RawQuery = "action=" + action
	var payload bytes.Buffer
	for i, doc := range docs {
		id, ok := doc["id"].(string)
		if !ok || id == "" {
			return nil, fmt.Errorf("Typesense document %d has no ID", i)
		}
		encoded, err := json.Marshal(doc)
		if err != nil {
			return nil, fmt.Errorf("encode Typesense document %d: %w", i, err)
		}
		if i > 0 {
			payload.WriteByte('\n')
		}
		payload.Write(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), &payload)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-TYPESENSE-API-KEY", apiKey)
	req.Header.Set("Content-Type", "text/plain")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxImportResponseBytes+1))
	if err != nil {
		return nil, err
	}
	if len(body) > maxImportResponseBytes {
		return nil, errors.New("Typesense import response exceeds size limit")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("Typesense import returned HTTP %d", resp.StatusCode)
	}
	// Typesense may end its JSONL reply with one line terminator. That does
	// not represent an additional acknowledgement; any other extra line does.
	lines := strings.Split(strings.TrimSuffix(string(body), "\n"), "\n")
	if len(lines) != len(docs) {
		return nil, fmt.Errorf("Typesense import acknowledgement cardinality %d != %d", len(lines), len(docs))
	}
	failed := make(map[string]importFailure)
	for i, line := range lines {
		var result map[string]json.RawMessage
		if err := json.Unmarshal([]byte(line), &result); err != nil {
			return nil, fmt.Errorf("decode Typesense acknowledgement %d: %w", i, err)
		}
		raw, present := result["success"]
		if !present {
			return nil, fmt.Errorf("Typesense acknowledgement %d has no success field", i)
		}
		if string(raw) != "true" && string(raw) != "false" {
			return nil, fmt.Errorf("Typesense acknowledgement %d has non-boolean success", i)
		}
		if string(raw) == "false" {
			var failure importFailure
			_ = json.Unmarshal(result["error"], &failure.Reason)
			_ = json.Unmarshal(result["code"], &failure.Code)
			failed[docs[i]["id"].(string)] = failure
		}
	}
	return failed, nil
}
