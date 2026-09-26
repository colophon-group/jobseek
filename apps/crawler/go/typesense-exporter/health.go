package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

func probeTypesense(ctx context.Context, client *http.Client, baseURL, apiKey string) (bool, error) {
	deadline, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(deadline, http.MethodGet, baseURL+"/health", nil)
	if err != nil {
		return false, err
	}
	req.Header.Set("X-TYPESENSE-API-KEY", apiKey)
	response, err := client.Do(req)
	if err != nil {
		return false, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return false, fmt.Errorf("Typesense health returned HTTP %d", response.StatusCode)
	}
	var result struct {
		OK bool `json:"ok"`
	}
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		return false, fmt.Errorf("decode Typesense health: %w", err)
	}
	return result.OK, nil
}
