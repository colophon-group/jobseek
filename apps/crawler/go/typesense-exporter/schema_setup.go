package main

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"
)

//go:embed schema_contract.json
var schemaContractJSON []byte

type schemaDefinition struct {
	Name                string           `json:"name"`
	Fields              []map[string]any `json:"fields"`
	DefaultSortingField string           `json:"default_sorting_field,omitempty"`
	TokenSeparators     []string         `json:"token_separators,omitempty"`
	SymbolsToIndex      []string         `json:"symbols_to_index,omitempty"`
}

func loadSchemaDefinitions() ([]schemaDefinition, error) {
	var definitions []schemaDefinition
	if err := json.Unmarshal(schemaContractJSON, &definitions); err != nil {
		return nil, err
	}
	if len(definitions) != 7 {
		return nil, errors.New("incomplete Typesense schema contract")
	}
	return definitions, nil
}

type schemaRequestError struct {
	Status  int
	Busy    bool
	Timeout bool
}

func (err *schemaRequestError) Error() string {
	if err.Timeout {
		return "Typesense schema request timed out"
	}
	if err.Status != 0 {
		return fmt.Sprintf("Typesense schema HTTP %d", err.Status)
	}
	return "Typesense schema transport failure"
}
func schemaStatus(err error, status int) bool {
	var requestError *schemaRequestError
	return errors.As(err, &requestError) && requestError.Status == status
}

var schemaSafeName = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)

type schemaClient struct {
	HTTP         *http.Client
	BaseURL, Key string
}

func (client schemaClient) request(ctx context.Context, method, path string, payload any) (any, error) {
	if client.HTTP == nil || client.Key == "" {
		return nil, errors.New("missing Typesense schema client")
	}
	endpoint, err := url.Parse(client.BaseURL)
	if err != nil || (endpoint.Scheme != "http" && endpoint.Scheme != "https") || endpoint.Host == "" || endpoint.Path != "" || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return nil, errors.New("invalid Typesense schema origin")
	}
	endpoint.Path = path
	var body io.Reader
	if payload != nil {
		data, err := json.Marshal(payload)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(data)
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint.String(), body)
	if err != nil {
		return nil, errors.New("invalid Typesense schema request")
	}
	request.Header.Set("X-TYPESENSE-API-KEY", client.Key)
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := client.HTTP.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		var networkError net.Error
		return nil, &schemaRequestError{Timeout: errors.As(err, &networkError) && networkError.Timeout()}
	}
	defer response.Body.Close()
	const limit = 4 << 20
	data, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		var networkError net.Error
		return nil, &schemaRequestError{Timeout: errors.As(err, &networkError) && networkError.Timeout()}
	}
	if len(data) > limit || !utf8.Valid(data) {
		return nil, errors.New("invalid or oversized Typesense schema response")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, &schemaRequestError{Status: response.StatusCode, Busy: response.StatusCode == 422 && strings.Contains(strings.ToLower(string(data)), "another collection update operation is in progress")}
	}
	if len(bytes.TrimSpace(data)) == 0 {
		return "", nil
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, errors.New("invalid Typesense schema JSON")
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return nil, errors.New("trailing Typesense schema JSON")
	}
	return value, nil
}
func schemaObject(value any) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, errors.New("invalid Typesense schema object")
	}
	return object, nil
}
func schemaFieldList(value any) ([]map[string]any, error) {
	fields, ok := value.([]any)
	if !ok {
		return nil, errors.New("invalid Typesense schema fields")
	}
	result := make([]map[string]any, 0, len(fields))
	for _, item := range fields {
		field, ok := item.(map[string]any)
		if !ok {
			return nil, errors.New("invalid Typesense schema field")
		}
		name, ok := field["name"].(string)
		if !ok || name == "" {
			return nil, errors.New("invalid Typesense field name")
		}
		result = append(result, field)
	}
	return result, nil
}

type schemaPatch struct {
	Fields         []map[string]any
	Added, Rebuilt []string
	Deferred       bool
}

func schemaPatchFields(live, desired []map[string]any) schemaPatch {
	byName := map[string]map[string]any{}
	for _, field := range live {
		byName[field["name"].(string)] = field
	}
	missing, rebuild := []map[string]any{}, []map[string]any{}
	patch := schemaPatch{Fields: []map[string]any{}, Added: []string{}, Rebuilt: []string{}}
	for _, field := range desired {
		name := field["name"].(string)
		if name == "id" {
			continue
		}
		existing, exists := byName[name]
		if !exists {
			missing = append(missing, field)
			patch.Added = append(patch.Added, name)
			continue
		}
		a, exists := existing["index"]
		if !exists {
			a = true
		}
		b, exists := field["index"]
		if !exists {
			b = true
		}
		if a != b {
			rebuild = append(rebuild, field)
		}
	}
	if len(rebuild) > 0 {
		field := rebuild[0]
		patch.Fields = append(patch.Fields, map[string]any{"name": field["name"], "drop": true}, field)
		patch.Rebuilt = append(patch.Rebuilt, field["name"].(string))
	}
	patch.Fields = append(patch.Fields, missing...)
	patch.Deferred = len(rebuild) > 1
	return patch
}
func schemaChangesActive(value any) (active, known bool) {
	switch value := value.(type) {
	case nil:
		return false, false
	case string:
		return strings.TrimSpace(value) != "", true
	case []any:
		return len(value) > 0, true
	case map[string]any:
		if len(value) == 0 {
			return false, true
		}
		for _, key := range []string{"schema_changes", "operations", "results"} {
			if list, ok := value[key].([]any); ok {
				return len(list) > 0, true
			}
		}
		return true, true
	case bool:
		return value, true
	case json.Number:
		number, err := strconv.ParseFloat(string(value), 64)
		return err != nil || number != 0, true
	default:
		return true, true
	}
}
func schemaSleep(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

type schemaSetup struct {
	Client                                 schemaClient
	Deadline, Poll, InitialRetry, MaxRetry time.Duration
	Sleep                                  func(context.Context, time.Duration) error
}

func newSchemaSetup(client schemaClient) *schemaSetup {
	return &schemaSetup{Client: client, Deadline: 2 * time.Hour, Poll: 5 * time.Second, InitialRetry: 2 * time.Second, MaxRetry: 30 * time.Second, Sleep: schemaSleep}
}
func (setup *schemaSetup) waitClear(ctx context.Context) (bool, error) {
	for {
		if err := ctx.Err(); err != nil {
			return false, err
		}
		response, err := setup.Client.request(ctx, http.MethodGet, "/operations/schema_changes", nil)
		if err != nil {
			if ctx.Err() != nil {
				return false, ctx.Err()
			}
			slog.Info("typesense.schema_changes.unavailable")
			return false, nil
		}
		active, known := schemaChangesActive(response)
		if !known {
			return false, nil
		}
		if !active {
			return true, nil
		}
		slog.Info("typesense.collection.schema_alter_wait", "sleep_seconds", setup.Poll.Seconds())
		if err := setup.Sleep(ctx, setup.Poll); err != nil {
			return false, err
		}
	}
}
func (setup *schemaSetup) patch(ctx context.Context, name string, desired []map[string]any) error {
	if !schemaSafeName.MatchString(name) {
		return errors.New("invalid Typesense collection name")
	}
	ctx, cancel := context.WithTimeout(ctx, setup.Deadline)
	defer cancel()
	delay := setup.InitialRetry
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		response, err := setup.Client.request(ctx, http.MethodGet, "/collections/"+name, nil)
		if schemaStatus(err, 404) {
			return nil
		}
		if err != nil {
			return err
		}
		live, err := schemaObject(response)
		if err != nil {
			return err
		}
		fields, err := schemaFieldList(live["fields"])
		if err != nil {
			return err
		}
		byName := map[string]map[string]any{}
		for _, field := range fields {
			byName[field["name"].(string)] = field
		}
		for _, field := range desired {
			if old, exists := byName[field["name"].(string)]; exists && old["type"] != field["type"] {
				slog.Warn("typesense.schema.field_drift", "collection", name, "field", field["name"], "recovery", "drop + re-add field + backfill")
			}
		}
		patch := schemaPatchFields(fields, desired)
		if len(patch.Fields) == 0 {
			slog.Info("typesense.collection.up_to_date", "collection", name)
			return nil
		}
		slog.Info("typesense.collection.patching", "collection", name, "added", patch.Added, "rebuilt", patch.Rebuilt, "deferred_rebuilds", patch.Deferred)
		_, err = setup.Client.request(ctx, http.MethodPatch, "/collections/"+name, map[string]any{"fields": patch.Fields})
		if err == nil {
			if patch.Deferred {
				continue
			}
			return nil
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		var requestError *schemaRequestError
		if !errors.As(err, &requestError) || (!requestError.Busy && !requestError.Timeout) {
			return err
		}
		// An ambiguous synchronous PATCH must be observed before it can be retried.
		// Every retry re-reads the schema and recomputes the remaining field changes.
		clear, err := setup.waitClear(ctx)
		if err != nil {
			return err
		}
		if !clear || requestError.Timeout {
			if err := setup.Sleep(ctx, delay); err != nil {
				return err
			}
			delay = min(delay*2, setup.MaxRetry)
		}
	}
}
func (setup *schemaSetup) collections(ctx context.Context, definitions []schemaDefinition, force bool) error {
	for _, definition := range definitions {
		name := definition.Name
		if !schemaSafeName.MatchString(name) {
			return errors.New("invalid schema contract collection")
		}
		versioned := name + "_v1"
		slog.Info("typesense.setup.collection.start", "alias", name)
		if force {
			for _, path := range []string{"/aliases/" + name, "/collections/" + versioned} {
				if _, err := setup.Client.request(ctx, http.MethodDelete, path, nil); err != nil && !schemaStatus(err, 404) {
					return err
				}
			}
		}
		response, err := setup.Client.request(ctx, http.MethodGet, "/aliases/"+name, nil)
		if err != nil && !schemaStatus(err, 404) {
			return err
		}
		if err == nil {
			alias, err := schemaObject(response)
			if err != nil {
				return err
			}
			if target, ok := alias["collection_name"].(string); ok && target != "" {
				if err := setup.patch(ctx, target, definition.Fields); err != nil {
					return err
				}
				continue
			}
		}
		_, err = setup.Client.request(ctx, http.MethodGet, "/collections/"+versioned, nil)
		if err != nil && !schemaStatus(err, 404) {
			return err
		}
		if err == nil {
			if err := setup.patch(ctx, versioned, definition.Fields); err != nil {
				return err
			}
		} else {
			definition.Name = versioned
			if _, err := setup.Client.request(ctx, http.MethodPost, "/collections", definition); err != nil && !schemaStatus(err, 409) {
				return err
			}
		}
		if _, err := setup.Client.request(ctx, http.MethodPut, "/aliases/"+name, map[string]any{"collection_name": versioned}); err != nil {
			return err
		}
		slog.Info("typesense.alias.created", "alias", name, "target", versioned)
	}
	return nil
}

var schemaMemoryKeys = []string{"typesense_memory_allocated_bytes", "typesense_memory_active_bytes", "typesense_memory_resident_bytes"}

func (setup *schemaSetup) memory(ctx context.Context) map[string]int64 {
	response, err := setup.Client.request(ctx, http.MethodGet, "/metrics.json", nil)
	if err != nil {
		slog.Warn("typesense.setup.metrics_error")
		return nil
	}
	object, err := schemaObject(response)
	if err != nil {
		return nil
	}
	result := map[string]int64{}
	for _, key := range schemaMemoryKeys {
		if value, exists := object[key]; exists && value != nil {
			number, err := strconv.ParseInt(fmt.Sprint(value), 10, 64)
			if err != nil {
				return nil
			}
			result[key] = number
		}
	}
	return result
}
func schemaSettings() (schemaClient, error) {
	protocol, host, port, key := os.Getenv("TYPESENSE_PROTOCOL"), os.Getenv("TYPESENSE_HOST"), os.Getenv("TYPESENSE_PORT"), os.Getenv("TYPESENSE_OPERATIONS_KEY")
	if protocol == "" {
		protocol = "http"
	}
	if port == "" {
		port = "8108"
	}
	number, err := strconv.Atoi(port)
	if err != nil || number < 1 || number > 65535 || (protocol != "http" && protocol != "https") || host == "" || key == "" {
		return schemaClient{}, errors.New("Typesense operations configuration is required")
	}
	return schemaClient{HTTP: &http.Client{Timeout: time.Hour, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, BaseURL: protocol + "://" + net.JoinHostPort(host, strconv.Itoa(number)), Key: key}, nil
}
func runSchemaSetup(force bool) error {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stderr, nil)))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	client, err := schemaSettings()
	if err != nil {
		return err
	}
	definitions, err := loadSchemaDefinitions()
	if err != nil {
		return err
	}
	setup := newSchemaSetup(client)
	response, err := client.request(ctx, http.MethodGet, "/health", nil)
	if err != nil {
		return err
	}
	health, err := schemaObject(response)
	if err != nil || health["ok"] != true {
		return errors.New("Typesense reports unhealthy")
	}
	slog.Info("typesense.setup.healthy")
	before := setup.memory(ctx)
	if err := setup.collections(ctx, definitions, force); err != nil {
		return err
	}
	after := setup.memory(ctx)
	if len(before) > 0 && len(after) > 0 {
		delta := map[string]int64{}
		for _, key := range schemaMemoryKeys {
			_, a := before[key]
			_, b := after[key]
			if a || b {
				delta[key] = after[key] - before[key]
			}
		}
		slog.Info("typesense.setup.memory_delta", "before", before, "after", after, "delta", delta)
	}
	slog.Info("typesense.setup.done")
	return nil
}
