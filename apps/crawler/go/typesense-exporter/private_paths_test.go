package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func privatePathTestEnvironment() map[string]string {
	return map[string]string{
		"JOBSEEK_EXPECTED_POSTGRES_PRIVATE_IP":  "10.0.0.2",
		"JOBSEEK_EXPECTED_TYPESENSE_PRIVATE_IP": "10.0.0.3",
		"LOCAL_DATABASE_URL":                    "postgresql://fixture:private-password@10.0.0.2:5432/crawler?sslmode=disable",
		"TYPESENSE_HOST":                        "10.0.0.3", "TYPESENSE_PORT": "8108", "TYPESENSE_PROTOCOL": "http",
	}
}

func TestPrivatePathSettingsRejectDifferentOrPublicDestinations(t *testing.T) {
	for _, test := range []struct{ name, key, value string }{
		{"public expected database", "JOBSEEK_EXPECTED_POSTGRES_PRIVATE_IP", "203.0.113.1"},
		{"IPv6", "JOBSEEK_EXPECTED_TYPESENSE_PRIVATE_IP", "fd00::1"},
		{"invalid address", "JOBSEEK_EXPECTED_TYPESENSE_PRIVATE_IP", "10.example.invalid"},
		{"wrong database", "LOCAL_DATABASE_URL", "postgresql://fixture:private-password@10.0.0.9/crawler"},
		{"database fallback", "LOCAL_DATABASE_URL", "postgresql://fixture:private-password@10.0.0.2,10.0.0.9/crawler"},
		{"socket", "LOCAL_DATABASE_URL", "postgresql:///crawler?host=/tmp"},
		{"invalid database port", "LOCAL_DATABASE_URL", "postgresql://fixture:private-password@10.0.0.2:0/crawler"},
		{"query host override", "LOCAL_DATABASE_URL", "postgresql://fixture:private-password@10.0.0.2/crawler?host=203.0.113.1"},
		{"empty database", "LOCAL_DATABASE_URL", ""},
		{"keyword DSN", "LOCAL_DATABASE_URL", "host=10.0.0.2 user=fixture password=private-password"},
		{"wrong search", "TYPESENSE_HOST", "10.0.0.9"},
		{"search URL injection", "TYPESENSE_HOST", "10.0.0.3/redirect"},
		{"wrong protocol", "TYPESENSE_PROTOCOL", "https"},
		{"invalid port", "TYPESENSE_PORT", "0"},
		{"oversized port", "TYPESENSE_PORT", "65536"},
	} {
		t.Run(test.name, func(t *testing.T) {
			env := privatePathTestEnvironment()
			env[test.key] = test.value
			_, err := privatePathSettings(func(key string) string { return env[key] })
			if err == nil || strings.Contains(err.Error(), "private-password") || strings.Contains(err.Error(), "10.0.0.") {
				t.Fatalf("unsafe or missing rejection: %v", err)
			}
		})
	}
	env := privatePathTestEnvironment()
	c, err := privatePathSettings(func(key string) string { return env[key] })
	if err != nil || c.search != "http://10.0.0.3:8108/health" || c.postgres.ConnectTimeout != 10*time.Second || c.postgres.DefaultQueryExecMode != pgx.QueryExecModeExec {
		t.Fatal("valid private-path settings were not retained")
	}
	if c.postgres.RuntimeParams["application_name"] != "jobseek:ingress:private-path-verifier" || c.postgres.RuntimeParams["idle_in_transaction_session_timeout"] != "60s" {
		t.Fatal("original probe startup parameters changed")
	}
	for _, key := range []string{"default_transaction_read_only", "statement_timeout"} {
		if _, exists := c.postgres.RuntimeParams[key]; exists {
			t.Fatalf("new startup parameter %s could break pooler compatibility", key)
		}
	}
}

type privatePathTestRow struct {
	value int
	err   error
}

func (r privatePathTestRow) Scan(dest ...any) error {
	if r.err != nil {
		return r.err
	}
	*dest[0].(*int) = r.value
	return nil
}

type privatePathTestConnection struct {
	row           privatePathTestRow
	query         string
	closed        bool
	queryDeadline bool
	closeDeadline bool
	closeErr      error
}

func (c *privatePathTestConnection) QueryRow(ctx context.Context, query string, _ ...any) pgx.Row {
	c.query = query
	deadline, ok := ctx.Deadline()
	c.queryDeadline = ok && time.Until(deadline) <= 30*time.Second
	if ctx.Err() != nil {
		return privatePathTestRow{err: ctx.Err()}
	}
	return c.row
}

func (c *privatePathTestConnection) Close(ctx context.Context) error {
	c.closed = ctx.Err() == nil
	deadline, ok := ctx.Deadline()
	c.closeDeadline = ok && time.Until(deadline) <= 2*time.Second
	return c.closeErr
}

func TestPrivatePostgresProbeUsesOnlyBoundedSelectAndClosesOnEveryQueryOutcome(t *testing.T) {
	for _, test := range []struct {
		name       string
		row        privatePathTestRow
		connectErr error
		valid      bool
	}{
		{"success", privatePathTestRow{value: 1}, nil, true},
		{"wrong value", privatePathTestRow{value: 2}, nil, false},
		{"query failure", privatePathTestRow{err: errors.New("private-password query error")}, nil, false},
		{"connect failure", privatePathTestRow{}, errors.New("private-password connect error"), false},
	} {
		t.Run(test.name, func(t *testing.T) {
			connection := &privatePathTestConnection{row: test.row}
			err := verifyPrivatePostgres(context.Background(), &pgx.ConnConfig{}, func(ctx context.Context, _ *pgx.ConnConfig) (privatePathConnection, error) {
				deadline, ok := ctx.Deadline()
				if !ok || time.Until(deadline) > 10*time.Second {
					t.Fatal("unbounded connection")
				}
				return connection, test.connectErr
			})
			if (err == nil) != test.valid || err != nil && strings.Contains(err.Error(), "private-password") {
				t.Fatalf("unsafe probe result: %v", err)
			}
			if test.connectErr == nil && (connection.query != "SELECT 1" || !connection.closed || !connection.queryDeadline || !connection.closeDeadline) {
				t.Fatal("query or cleanup contract changed")
			}
		})
	}
}

func TestPrivatePostgresCancellationAndCleanupFailureStaySafe(t *testing.T) {
	for _, cancelBeforeQuery := range []bool{false, true} {
		ctx, cancel := context.WithCancel(context.Background())
		connection := &privatePathTestConnection{row: privatePathTestRow{value: 1}}
		if !cancelBeforeQuery {
			connection.closeErr = errors.New("private-password cleanup error")
		}
		err := verifyPrivatePostgres(ctx, &pgx.ConnConfig{}, func(context.Context, *pgx.ConnConfig) (privatePathConnection, error) {
			if cancelBeforeQuery {
				cancel()
			}
			return connection, nil
		})
		cancel()
		if err == nil || strings.Contains(err.Error(), "private-password") || !connection.closed || !connection.closeDeadline {
			t.Fatalf("unsafe cancellation/cleanup result: %v", err)
		}
	}
}

func TestPrivateTypesenseProbeDoesNotFollowRedirectsOrLeakCredentials(t *testing.T) {
	for _, test := range []struct {
		name, body string
		status     int
		valid      bool
	}{
		{"success", `{"ok":true}`, 200, true},
		{"unhealthy", `{"ok":false}`, 200, false},
		{"wrong type", `{"ok":"true"}`, 200, false},
		{"redirect", `{"ok":true}`, 302, false},
		{"server failure", `{"ok":true}`, 503, false},
		{"trailing bytes", `{"ok":true} {}`, 200, false},
		{"oversized", `{"ok":true,"padding":"` + strings.Repeat("x", 4096) + `"}`, 200, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			var requests atomic.Int64
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests.Add(1)
				if r.Method != "GET" || r.URL.Path != "/health" || r.Header.Get("Authorization") != "" || r.Header.Get("X-TYPESENSE-API-KEY") != "" {
					t.Error("private health request changed")
				}
				w.Header().Set("Location", "/must-not-follow")
				w.WriteHeader(test.status)
				_, _ = w.Write([]byte(test.body))
			}))
			defer server.Close()
			client := newPrivatePathHTTPClient()
			defer client.CloseIdleConnections()
			if client.Transport.(*http.Transport).Proxy != nil || client.Timeout != 10*time.Second {
				t.Fatal("unbounded or proxied private HTTP")
			}
			err := verifyPrivateTypesense(context.Background(), client, server.URL+"/health")
			if (err == nil) != test.valid || requests.Load() != 1 {
				t.Fatalf("result %v, requests %d", err, requests.Load())
			}
		})
	}
}

func TestPrivateTypesenseCancellationInterruptsResponseBody(t *testing.T) {
	started := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		close(started)
		<-r.Context().Done()
	}))
	defer server.Close()
	client := newPrivatePathHTTPClient()
	defer client.CloseIdleConnections()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- verifyPrivateTypesense(ctx, client, server.URL+"/health") }()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("private health request did not start")
	}
	cancel()
	select {
	case err := <-done:
		if err == nil || strings.Contains(err.Error(), server.URL) {
			t.Fatalf("unsafe cancellation result: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("private health body ignored cancellation")
	}
}

func TestPrivatePathCLIRejectsInvalidArgumentsAndConfigurationWithoutIO(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	binary := filepath.Join(t.TempDir(), "exporter")
	if output, err := exec.CommandContext(ctx, "go", "build", "-o", binary, ".").CombinedOutput(); err != nil {
		t.Fatalf("build private-path CLI: %v: %s", err, output)
	}
	for _, test := range []struct {
		name string
		args []string
		env  []string
		want string
	}{
		{"extra arguments", []string{"--verify-private-paths", "unexpected"}, nil, "usage: --verify-private-paths"},
		{"missing configuration", []string{"--verify-private-paths"}, nil, "requires private service IPv4 values"},
		{"wrong private host", []string{"--verify-private-paths"}, []string{
			"JOBSEEK_EXPECTED_POSTGRES_PRIVATE_IP=10.0.0.2", "JOBSEEK_EXPECTED_TYPESENSE_PRIVATE_IP=10.0.0.3",
			"LOCAL_DATABASE_URL=postgresql://fixture:private-password@10.0.0.9/crawler",
		}, "does not use the expected private host"},
	} {
		t.Run(test.name, func(t *testing.T) {
			command := exec.CommandContext(ctx, binary, test.args...)
			command.Env = append([]string{"GOTOOLCHAIN=local"}, test.env...)
			output, err := command.CombinedOutput()
			if err == nil || !strings.Contains(string(output), test.want) || strings.Contains(string(output), "private-password") || strings.Contains(string(output), "10.0.0.") {
				t.Fatalf("unsafe CLI result: %v: %s", err, output)
			}
		})
	}
}
