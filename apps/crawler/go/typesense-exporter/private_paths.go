package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
)

type privatePathConfig struct {
	postgres *pgx.ConnConfig
	search   string
}

func privatePathSettings(getenv func(string) string) (privatePathConfig, error) {
	var result privatePathConfig
	addresses := make([]netip.Addr, 2)
	for i, key := range []string{"JOBSEEK_EXPECTED_POSTGRES_PRIVATE_IP", "JOBSEEK_EXPECTED_TYPESENSE_PRIVATE_IP"} {
		address, err := netip.ParseAddr(getenv(key))
		if err != nil || !address.Is4() || !address.IsPrivate() {
			return result, errors.New("private-path verification requires private service IPv4 values")
		}
		addresses[i] = address
	}
	databaseURL := getenv("LOCAL_DATABASE_URL")
	// Match the previous URL-based verifier before asking the driver to load
	// TLS/password settings for this existing consumer configuration.
	parsed, err := url.Parse(databaseURL)
	if err != nil || (parsed.Scheme != "postgres" && parsed.Scheme != "postgresql") || parsed.Hostname() != addresses[0].String() {
		return result, errors.New("crawler PostgreSQL DSN does not use the expected private host")
	}
	port, err := strconv.Atoi(getenv("TYPESENSE_PORT"))
	if getenv("TYPESENSE_HOST") != addresses[1].String() || getenv("TYPESENSE_PROTOCOL") != "http" || err != nil || port < 1 || port > 65535 {
		return result, errors.New("crawler Typesense configuration does not use the expected private host")
	}
	config, err := pgx.ParseConfig(databaseURL)
	if err != nil || config.Host != addresses[0].String() || config.Port == 0 {
		return result, errors.New("crawler PostgreSQL DSN does not use the expected private host")
	}
	for _, fallback := range config.Fallbacks {
		if fallback.Host != config.Host || fallback.Port != config.Port {
			return result, errors.New("crawler PostgreSQL fallback does not use the expected private host")
		}
	}
	config.ConnectTimeout = 10 * time.Second
	config.DefaultQueryExecMode = pgx.QueryExecModeExec
	config.StatementCacheCapacity = 0
	config.DescriptionCacheCapacity = 0
	config.RuntimeParams["application_name"] = "jobseek:ingress:private-path-verifier"
	// Preserve the original probe's startup parameters. The fixed SELECT below
	// is read-only; adding session settings here can break pooler startup.
	config.RuntimeParams["idle_in_transaction_session_timeout"] = "60s"
	result.postgres = config
	result.search = "http://" + net.JoinHostPort(addresses[1].String(), strconv.Itoa(port)) + "/health"
	return result, nil
}

type privatePathConnection interface {
	QueryRow(context.Context, string, ...any) pgx.Row
	Close(context.Context) error
}

func verifyPrivatePostgres(ctx context.Context, config *pgx.ConnConfig, connect func(context.Context, *pgx.ConnConfig) (privatePathConnection, error)) (probeErr error) {
	connectionCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	connection, err := connect(connectionCtx, config)
	cancel()
	if err != nil {
		return errors.New("PostgreSQL private-path connection failed")
	}
	defer func() {
		closeCtx, closeCancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
		defer closeCancel()
		if err := connection.Close(closeCtx); err != nil && probeErr == nil {
			probeErr = errors.New("PostgreSQL private-path connection cleanup failed")
		}
	}()
	queryCtx, queryCancel := context.WithTimeout(ctx, 30*time.Second)
	defer queryCancel()
	var value int
	if err := connection.QueryRow(queryCtx, "SELECT 1").Scan(&value); err != nil || value != 1 {
		return errors.New("PostgreSQL private-path query failed")
	}
	return nil
}

func newPrivatePathHTTPClient() *http.Client {
	return &http.Client{
		Timeout: 10 * time.Second,
		Transport: &http.Transport{
			Proxy:       nil,
			DialContext: (&net.Dialer{Timeout: 10 * time.Second}).DialContext,
		},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

func verifyPrivateTypesense(ctx context.Context, client *http.Client, endpoint string) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return errors.New("Typesense private-path request configuration failed")
	}
	response, err := client.Do(request)
	if err != nil {
		return errors.New("Typesense private-path health request failed")
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 4097))
	var health struct {
		OK bool `json:"ok"`
	}
	if err != nil || len(body) > 4096 || response.StatusCode != http.StatusOK || json.Unmarshal(body, &health) != nil || !health.OK {
		return errors.New("Typesense private-path health check failed")
	}
	return nil
}

func runVerifyPrivatePaths() error {
	config, err := privatePathSettings(os.Getenv)
	if err != nil {
		return err
	}
	parent, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	// Leave two seconds for closing a connection after cancellation, keeping the
	// one-shot command within 55 seconds including its cleanup reserve.
	ctx, cancel := context.WithTimeout(parent, 53*time.Second)
	defer cancel()
	if err := verifyPrivatePostgres(ctx, config.postgres, func(ctx context.Context, config *pgx.ConnConfig) (privatePathConnection, error) {
		return pgx.ConnectConfig(ctx, config)
	}); err != nil {
		return err
	}
	client := newPrivatePathHTTPClient()
	defer client.CloseIdleConnections()
	if err := verifyPrivateTypesense(ctx, client, config.search); err != nil {
		return err
	}
	if ctx.Err() != nil {
		return errors.New("private-path verification canceled")
	}
	fmt.Println("Crawler private PostgreSQL query and Typesense health checks passed")
	return nil
}
