package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestSchemaContractPreservesEveryCreateAttribute(t *testing.T) {
	definitions, err := loadSchemaDefinitions()
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(definitions)
	if err != nil {
		t.Fatal(err)
	}
	var actual, expected any
	if err := json.Unmarshal(encoded, &actual); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(schemaContractJSON, &expected); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(actual, expected) {
		t.Fatal("schema serialization dropped create attributes")
	}
}
func TestSchemaPatchOneRebuildWithAllMissingFields(t *testing.T) {
	live := []map[string]any{{"name": "slug", "type": "string", "index": false}, {"name": "payload", "type": "string"}, {"name": "salary", "type": "int64"}}
	desired := []map[string]any{{"name": "id", "type": "string", "index": false}, {"name": "slug", "type": "string"}, {"name": "payload", "type": "string", "index": false}, {"name": "salary", "type": "int32"}, {"name": "new", "type": "string", "optional": true}}
	patch := schemaPatchFields(live, desired)
	if !patch.Deferred || !reflect.DeepEqual(patch.Added, []string{"new"}) || !reflect.DeepEqual(patch.Rebuilt, []string{"slug"}) {
		t.Fatalf("bad patch: %#v", patch)
	}
	if len(patch.Fields) != 3 || patch.Fields[0]["drop"] != true || patch.Fields[0]["name"] != "slug" || patch.Fields[2]["name"] != "new" {
		t.Fatal("patch rebuilt more than one field or lost additions")
	}
	live[0]["index"] = true
	live = append(live, desired[4])
	patch = schemaPatchFields(live, desired)
	if patch.Deferred || !reflect.DeepEqual(patch.Rebuilt, []string{"payload"}) || len(patch.Fields) != 2 {
		t.Fatal("deferred field not repaired independently")
	}
	live[1]["index"] = false
	patch = schemaPatchFields(live, desired)
	if len(patch.Fields) != 0 {
		t.Fatal("implicit id or type drift was auto-rebuilt")
	}
}
func TestSchemaSetupKeepsExistingAliasTarget(t *testing.T) {
	var mu sync.Mutex
	live := []map[string]any{{"name": "a", "type": "string", "index": false}, {"name": "b", "type": "string", "index": false}}
	patches := 0
	paths := []string{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		paths = append(paths, r.Method+" "+r.URL.Path)
		if r.Header.Get("X-TYPESENSE-API-KEY") != "fixture" {
			t.Error("credential missing")
		}
		switch r.Method + " " + r.URL.Path {
		case "GET /aliases/example":
			fmt.Fprint(w, `{"collection_name":"example_v9"}`)
		case "GET /collections/example_v9":
			json.NewEncoder(w).Encode(map[string]any{"fields": live})
		case "PATCH /collections/example_v9":
			var payload struct {
				Fields []map[string]any `json:"fields"`
			}
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Error(err)
			}
			patches++
			drops := 0
			for _, field := range payload.Fields {
				if field["drop"] == true {
					drops++
				}
			}
			if drops != 1 {
				t.Error("multiple field rebuilds in one PATCH")
			}
			target := payload.Fields[0]["name"].(string)
			for _, field := range live {
				if field["name"] == target {
					field["index"] = true
				}
			}
			if patches == 1 {
				live = append(live, map[string]any{"name": "new", "type": "string"})
			}
			fmt.Fprint(w, `{}`)
		default:
			t.Error("unexpected mutation: " + r.Method + " " + r.URL.Path)
			http.Error(w, "unexpected", 500)
		}
	}))
	defer server.Close()
	setup := newSchemaSetup(schemaClient{HTTP: server.Client(), BaseURL: server.URL, Key: "fixture"})
	err := setup.collections(context.Background(), []schemaDefinition{{Name: "example", Fields: []map[string]any{{"name": "a", "type": "string"}, {"name": "b", "type": "string"}, {"name": "new", "type": "string"}}}}, false)
	if err != nil {
		t.Fatal(err)
	}
	if patches != 2 || len(paths) != 5 {
		t.Fatalf("unexpected convergence: %d %#v", patches, paths)
	}
}
func TestSchemaCreateRaceAndExplicitForce(t *testing.T) {
	definitions, err := loadSchemaDefinitions()
	if err != nil {
		t.Fatal(err)
	}
	for _, force := range []bool{false, true} {
		t.Run(fmt.Sprint(force), func(t *testing.T) {
			methods := []string{}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				methods = append(methods, r.Method+" "+r.URL.Path)
				switch r.Method {
				case "GET", "DELETE":
					http.NotFound(w, r)
				case "POST":
					var value schemaDefinition
					if err := json.NewDecoder(r.Body).Decode(&value); err != nil {
						t.Error(err)
					}
					if value.Name != "job_posting_v1" || !reflect.DeepEqual(value.TokenSeparators, definitions[0].TokenSeparators) {
						t.Error("create contract changed")
					}
					http.Error(w, "concurrent creation", 409)
				case "PUT":
					fmt.Fprint(w, `{}`)
				default:
					http.Error(w, "unexpected", 500)
				}
			}))
			defer server.Close()
			setup := newSchemaSetup(schemaClient{HTTP: server.Client(), BaseURL: server.URL, Key: "fixture"})
			if err := setup.collections(context.Background(), definitions[:1], force); err != nil {
				t.Fatal(err)
			}
			expected := []string{"GET /aliases/job_posting", "GET /collections/job_posting_v1", "POST /collections", "PUT /aliases/job_posting"}
			if force {
				expected = append([]string{"DELETE /aliases/job_posting", "DELETE /collections/job_posting_v1"}, expected...)
			}
			if !reflect.DeepEqual(methods, expected) {
				t.Fatalf("unexpected force scope: %#v", methods)
			}
		})
	}
}

type schemaTimeout struct{}

func (schemaTimeout) Error() string   { return "credential-bearing timeout" }
func (schemaTimeout) Timeout() bool   { return true }
func (schemaTimeout) Temporary() bool { return true }

type schemaTransport func(*http.Request) (*http.Response, error)

func (f schemaTransport) RoundTrip(request *http.Request) (*http.Response, error) { return f(request) }

func TestSchemaAmbiguousPatchIsObservedBeforeRetry(t *testing.T) {
	for _, kind := range []string{"timeout_applied", "busy_applied", "busy_unsupported", "rejected"} {
		t.Run(kind, func(t *testing.T) {
			applied := false
			patches := 0
			polls := 0
			sleeps := []time.Duration{}
			sequence := []string{}
			client := &http.Client{Transport: schemaTransport(func(r *http.Request) (*http.Response, error) {
				sequence = append(sequence, r.Method+" "+r.URL.Path)
				status := 200
				body := `{}`
				switch r.Method + " " + r.URL.Path {
				case "GET /collections/example_v1":
					if applied {
						body = `{"fields":[{"name":"a","type":"string"}]}`
					} else {
						body = `{"fields":[]}`
					}
				case "PATCH /collections/example_v1":
					patches++
					if patches == 1 {
						if kind == "timeout_applied" {
							applied = true
							return nil, schemaTimeout{}
						}
						status = 422
						body = `{"message":"Another collection update operation is in progress"}`
						if kind == "busy_applied" {
							applied = true
						}
						if kind == "rejected" {
							body = `{"message":"credential-bearing invalid schema"}`
						}
					} else {
						applied = true
					}
				case "GET /operations/schema_changes":
					polls++
					if kind == "busy_unsupported" {
						status = 404
					} else if kind == "busy_applied" && polls == 1 {
						body = `[{"collection":"example_v1"}]`
					} else {
						body = `[]`
					}
				default:
					t.Error("unexpected request")
				}
				return &http.Response{StatusCode: status, Body: ioNopString(body), Header: http.Header{}, Request: r}, nil
			})}
			setup := newSchemaSetup(schemaClient{HTTP: client, BaseURL: "http://example.test", Key: "fixture"})
			setup.Sleep = func(_ context.Context, delay time.Duration) error { sleeps = append(sleeps, delay); return nil }
			err := setup.patch(context.Background(), "example_v1", []map[string]any{{"name": "a", "type": "string"}})
			if kind == "rejected" {
				if err == nil || polls != 0 || patches != 1 || strings.Contains(err.Error(), "credential") {
					t.Fatal("rejection retried or leaked")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			expected := 1
			if kind == "busy_unsupported" {
				expected = 2
			}
			if patches != expected {
				t.Fatalf("ambiguous PATCH replayed: %#v", sequence)
			}
			if len(sequence) < 4 || sequence[2] != "GET /operations/schema_changes" {
				t.Fatal("PATCH not observed")
			}
			if len(sleeps) != 1 {
				t.Fatalf("backoff/poll changed: %#v", sleeps)
			}
		})
	}
}

// A tiny body helper keeps transport fixtures independent of a live network.
func ioNopString(value string) *schemaStringBody { return &schemaStringBody{strings.NewReader(value)} }

type schemaStringBody struct{ *strings.Reader }

func (*schemaStringBody) Close() error { return nil }

func TestSchemaWaitHasCancellationAndDeadline(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, `[{"active":true}]`) }))
	defer server.Close()
	setup := newSchemaSetup(schemaClient{HTTP: server.Client(), BaseURL: server.URL, Key: "fixture"})
	setup.Poll = time.Millisecond
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	if _, err := setup.waitClear(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("deadline ignored: %v", err)
	}
}
func TestSchemaSettingsRequireNoDatabaseCredential(t *testing.T) {
	t.Setenv("LOCAL_DATABASE_URL", "")
	t.Setenv("TYPESENSE_HOST", "127.0.0.1")
	t.Setenv("TYPESENSE_OPERATIONS_KEY", "fixture")
	t.Setenv("TYPESENSE_PORT", "")
	t.Setenv("TYPESENSE_PROTOCOL", "")
	client, err := schemaSettings()
	if err != nil {
		t.Fatal(err)
	}
	if client.BaseURL != "http://127.0.0.1:8108" || client.HTTP.Timeout != time.Hour {
		t.Fatal("setup timeout or defaults changed")
	}
}
func TestSchemaChangesResponses(t *testing.T) {
	for _, test := range []struct {
		value         any
		active, known bool
	}{{nil, false, false}, {"", false, true}, {" [] ", true, true}, {[]any{}, false, true}, {[]any{1}, true, true}, {map[string]any{}, false, true}, {map[string]any{"operations": []any{}}, false, true}, {map[string]any{"results": []any{1}}, true, true}} {
		active, known := schemaChangesActive(test.value)
		if active != test.active || known != test.known {
			t.Fatalf("invalid status decoding %#v", test.value)
		}
	}
}
