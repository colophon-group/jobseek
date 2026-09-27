package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
)

func taxonomyReadiness(ctx context.Context) (map[string]any, error) {
	settings, err := loadExporterSettings()
	if err != nil {
		return nil, err
	}
	contract, err := loadTaxonomyContract()
	if err != nil {
		return nil, err
	}
	config, err := pgx.ParseConfig(settings.DBURL)
	if err != nil {
		return nil, errors.New("invalid taxonomy database configuration")
	}
	config.RuntimeParams["application_name"] = "jobseek:crawler:maintenance|taxonomy-verification:local"
	config.RuntimeParams["statement_timeout"] = "300000"
	config.RuntimeParams["idle_in_transaction_session_timeout"] = "60000"
	config.ConnectTimeout = 15 * time.Second
	conn, err := pgx.ConnectConfig(ctx, config)
	if err != nil {
		return nil, err
	}
	defer conn.Close(context.Background())
	documents, err := loadTaxonomySnapshot(ctx, conn, contract)
	if err != nil {
		return nil, err
	}
	client := &http.Client{Timeout: 120 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	return verifyTaxonomySnapshot(ctx, taxonomyReader{Client: client, BaseURL: settings.TypesenseURL, Key: settings.OperationsKey}, contract, documents, 250)
}

// Exactly one redacted JSON record; the replaced process owns signals and exit
// status. Authority/transport errors never disclose SQL, credentials or values.
func taxonomyCLI(ctx context.Context, output io.Writer, verify func(context.Context) (map[string]any, error)) int {
	evidence, err := verify(ctx)
	code := 0
	if err != nil {
		class := "verification_error"
		if ctx.Err() != nil {
			class = "interrupted"
			code = 130
		} else {
			code = 1
		}
		evidence = map[string]any{"command": "verify-typesense-taxonomies", "status": "error", "authority": "local_postgres", "error_class": class}
	} else if evidence["status"] != "ready" {
		code = 1
	}
	if err := json.NewEncoder(output).Encode(evidence); err != nil {
		return 1
	}
	return code
}
func runTaxonomyCLI() int {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return taxonomyCLI(ctx, os.Stdout, taxonomyReadiness)
}
