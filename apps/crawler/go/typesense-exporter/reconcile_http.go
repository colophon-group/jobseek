package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const reconciliationRecordLimit = 1024 * 1024

type reconciliationHTTP struct {
	Client       *http.Client
	BaseURL, Key string
	RetryDelay   time.Duration
}

type reconciliationAcquisitionError struct{ reason string }

func (e *reconciliationAcquisitionError) Error() string { return e.reason }

func (c reconciliationHTTP) request(ctx context.Context, method, path string, query url.Values) (*http.Response, error) {
	request, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(c.BaseURL, "/")+"/collections/job_posting/documents"+path, nil)
	if err != nil {
		return nil, errors.New("invalid Typesense reconciliation endpoint")
	}
	request.URL.RawQuery = query.Encode()
	request.Header.Set("X-TYPESENSE-API-KEY", c.Key)
	response, err := c.Client.Do(request)
	if err != nil {
		return nil, &reconciliationAcquisitionError{"Typesense reconciliation transport failed"}
	}
	return response, nil
}

// An acquisition attempt never publishes its partial state. Framing, transport,
// and transient status failures restart the complete export; decoded invariant
// failures abort immediately. Destructive consumers see only a complete EOF.
func (c reconciliationHTTP) exportAttempt(ctx context.Context, query url.Values, consume func(map[string]any) error) error {
	response, err := c.request(ctx, http.MethodGet, "/export", query)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		reason := fmt.Sprintf("Typesense reconciliation export status %d", response.StatusCode)
		switch response.StatusCode {
		case 408, 425, 429, 500, 502, 503, 504:
			return &reconciliationAcquisitionError{reason}
		default:
			return errors.New(reason)
		}
	}
	scanner := bufio.NewScanner(response.Body)
	scanner.Buffer(make([]byte, 16384), reconciliationRecordLimit+2)
	scanner.Split(func(data []byte, atEOF bool) (int, []byte, error) {
		if index := bytes.IndexByte(data, '\n'); index >= 0 {
			return index + 1, data[:index], nil
		}
		if atEOF && len(data) != 0 {
			return len(data), data, nil
		}
		return 0, nil, nil
	})
	for scanner.Scan() {
		record := scanner.Bytes()
		if len(record) == 0 {
			continue
		}
		if len(record) > reconciliationRecordLimit || !utf8.Valid(record) {
			return &reconciliationAcquisitionError{"Typesense reconciliation record violates framing bounds"}
		}
		decoder := json.NewDecoder(bytes.NewReader(record))
		decoder.UseNumber()
		var decoded any
		if err := decoder.Decode(&decoded); err != nil {
			return &reconciliationAcquisitionError{"invalid Typesense reconciliation JSON"}
		}
		if err := decoder.Decode(new(any)); err != io.EOF {
			return &reconciliationAcquisitionError{"invalid Typesense reconciliation JSON suffix"}
		}
		document, ok := decoded.(map[string]any)
		if !ok {
			return errors.New("Typesense reconciliation export returned a non-object document")
		}
		if err := consume(document); err != nil {
			return err
		}
	}
	if scanner.Err() != nil {
		return &reconciliationAcquisitionError{"incomplete Typesense reconciliation stream"}
	}
	return nil
}

func (c reconciliationHTTP) retryExport(ctx context.Context, attempt func() error) error {
	for i := 0; i < 3; i++ {
		err := attempt()
		if err == nil {
			return nil
		}
		var acquisition *reconciliationAcquisitionError
		if !errors.As(err, &acquisition) || i == 2 {
			return err
		}
		timer := time.NewTimer(c.RetryDelay * time.Duration(i+1))
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
	return errors.New("unreachable reconciliation retry state")
}

func (c reconciliationHTTP) partition(ctx context.Context, partition int) (reconciliationSnapshot, error) {
	if _, _, err := reconciliationBounds(partition); err != nil {
		return nil, err
	}
	bucket := fmt.Sprintf("%02x", partition)
	query := url.Values{
		"filter_by":      {"reconciliation_bucket:=" + bucket},
		"include_fields": {"id,is_active,reconciliation_bucket," + strings.Join(reconciliationPayloadFields, ",")},
		"batch_size":     {"1000"},
	}
	var snapshot reconciliationSnapshot
	err := c.retryExport(ctx, func() error {
		snapshot = reconciliationSnapshot{}
		return c.exportAttempt(ctx, query, func(document map[string]any) error {
			if document["reconciliation_bucket"] != bucket {
				return errors.New("Typesense document is in the wrong partition")
			}
			return addReconciliationDocument(snapshot, document)
		})
	})
	if err != nil {
		return nil, err
	}
	return snapshot, nil
}

type reconciliationUnbucketed struct {
	ID     string
	Active bool
}

func (c reconciliationHTTP) unbucketed(ctx context.Context) ([]reconciliationUnbucketed, error) {
	query := url.Values{"include_fields": {"id,is_active,reconciliation_bucket"}, "batch_size": {"1000"}}
	var candidates []reconciliationUnbucketed
	err := c.retryExport(ctx, func() error {
		candidates = nil
		idBytes := 0
		return c.exportAttempt(ctx, query, func(document map[string]any) error {
			id, ok := document["id"].(string)
			if !ok {
				return errors.New("Typesense reconciliation document has invalid ID")
			}
			active, ok := document["is_active"].(bool)
			if !ok {
				return errors.New("Typesense reconciliation document has invalid active state")
			}
			parsed, err := reconciliationUUID(id)
			if err == nil && document["reconciliation_bucket"] == parsed[:2] {
				return nil
			}
			idBytes += len(id)
			if len(candidates) >= 50000 || idBytes > 16*1024*1024 {
				return errors.New("Typesense unbucketed export exceeded its candidate safety limit")
			}
			candidates = append(candidates, reconciliationUnbucketed{id, active})
			return nil
		})
	})
	if err != nil {
		return nil, err
	}
	return candidates, nil
}

func (c reconciliationHTTP) deleteIDs(ctx context.Context, ids []string) error {
	semaphore := make(chan struct{}, 20)
	var workers sync.WaitGroup
	var mutex sync.Mutex
	failed := 0
	for _, id := range ids {
		select {
		case semaphore <- struct{}{}:
		case <-ctx.Done():
			workers.Wait()
			return ctx.Err()
		}
		workers.Add(1)
		go func(id string) {
			defer workers.Done()
			defer func() { <-semaphore }()
			response, err := c.request(ctx, http.MethodDelete, "/"+url.PathEscape(id), nil)
			if err == nil {
				defer response.Body.Close()
				if response.StatusCode != http.StatusNotFound && (response.StatusCode < 200 || response.StatusCode >= 300) {
					err = errors.New("delete rejected")
				}
			}
			if err != nil {
				mutex.Lock()
				failed++
				mutex.Unlock()
			}
		}(id)
	}
	workers.Wait() // No delete may outlive the caller's exporter fence.
	if failed != 0 {
		return fmt.Errorf("Typesense delete failed for %d reconciliation documents", failed)
	}
	return nil
}
