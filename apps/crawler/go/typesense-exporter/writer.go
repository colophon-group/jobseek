package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

var (
	errNotExportOwner  = errors.New("Go does not own the Typesense posting cursor")
	errTypesenseImport = errors.New("Typesense posting import failed")
)

const typesenseOwnerKey = "export_owner:typesense:job_posting"

type exporterSettings struct {
	DBURL         string
	TypesenseURL  string
	OperationsKey string
	BatchLimit    int
	Interval      time.Duration
	MetricsPort   int
	RedisURL      string
}

func loadExporterSettings() (exporterSettings, error) {
	c := exporterSettings{
		DBURL:         os.Getenv("LOCAL_DATABASE_URL"),
		OperationsKey: os.Getenv("TYPESENSE_OPERATIONS_KEY"),
		BatchLimit:    2000,
		Interval:      time.Second,
		MetricsPort:   9093,
		RedisURL:      os.Getenv("REDIS_URL"),
	}
	protocol, host, port := os.Getenv("TYPESENSE_PROTOCOL"), os.Getenv("TYPESENSE_HOST"), os.Getenv("TYPESENSE_PORT")
	if c.DBURL == "" || c.OperationsKey == "" || (protocol != "http" && protocol != "https") || host == "" || port == "" {
		return c, errors.New("local database and Typesense operations configuration are required")
	}
	portNumber, err := strconv.Atoi(port)
	if err != nil || portNumber < 1 || portNumber > 65535 {
		return c, errors.New("TYPESENSE_PORT must be valid")
	}
	c.TypesenseURL = fmt.Sprintf("%s://%s:%d", protocol, host, portNumber)
	if c.RedisURL == "" {
		c.RedisURL = "redis://localhost:6379/0"
	}
	if raw := os.Getenv("METRICS_PORT"); raw != "" {
		metricsPort, err := strconv.Atoi(raw)
		if err != nil || metricsPort < 1 || metricsPort > 65535 {
			return c, errors.New("METRICS_PORT must be valid")
		}
		c.MetricsPort = metricsPort
	}
	return c, nil
}

func requireGoOwner(ctx context.Context, conn *pgx.Conn) error {
	var owner string
	err := conn.QueryRow(ctx, "SELECT value FROM exporter_state WHERE key = $1", typesenseOwnerKey).Scan(&owner)
	if errors.Is(err, pgx.ErrNoRows) {
		return errNotExportOwner
	}
	if err != nil {
		return fmt.Errorf("read Typesense exporter owner: %w", err)
	}
	if owner != "go" {
		return errNotExportOwner
	}
	return nil
}

type exporter struct {
	conn         *pgx.Conn
	httpClient   *http.Client
	settings     exporterSettings
	maps         Maps
	mapsLoadedAt time.Time
	metrics      *exporterMetrics
}

type tickResult struct {
	Read           int
	Rejected       int
	Position       cursor
	ImportDuration time.Duration
}

func (e *exporter) tick(ctx context.Context) (tickResult, error) {
	var result tickResult
	err := withCursorFence(ctx, e.conn, func() error {
		if err := requireGoOwner(ctx, e.conn); err != nil {
			return err
		}
		position, err := loadCursor(ctx, e.conn)
		if err != nil {
			return err
		}
		result.Position = position
		if e.mapsLoadedAt.IsZero() || time.Since(e.mapsLoadedAt) > 10*time.Minute {
			maps, err := loadMaps(ctx, e.conn)
			if err != nil {
				return fmt.Errorf("refresh taxonomy maps: %w", err)
			}
			e.maps, e.mapsLoadedAt = maps, time.Now()
		}
		cutoff, err := captureSafeCutoff(ctx, e.conn, e.metrics)
		if err != nil {
			return err
		}
		rows, next, err := fetchPostings(ctx, e.conn, position, cutoff, e.settings.BatchLimit)
		if err != nil {
			return err
		}
		result.Read = len(rows)
		if len(rows) == 0 {
			return nil
		}
		docs := make([]map[string]any, 0, len(rows))
		for _, row := range rows {
			doc, err := project(row, e.maps)
			if err != nil {
				return fmt.Errorf("project Typesense posting: %w", err)
			}
			docs = append(docs, doc)
		}
		importStart := time.Now()
		failed, err := importDocs(ctx, e.httpClient, e.settings.TypesenseURL, e.settings.OperationsKey, docs)
		result.ImportDuration = time.Since(importStart)
		if err != nil {
			return fmt.Errorf("%w: %v", errTypesenseImport, err)
		}
		// Match the current Python exporter: deterministic per-document
		// rejections are logged and counted, then the batch cursor advances.
		// Whole-batch transport/protocol failures above keep it pinned.
		for id, failure := range failed {
			slog.Error("exporter.row_dropped", "target", "typesense", "posting_id", id,
				"error", failure.Reason, "code", failure.Code)
		}
		result.Rejected = len(failed)
		if err := saveCursor(ctx, e.conn, next); err != nil {
			return err
		}
		result.Position = next
		return nil
	})
	return result, err
}

func runExporter() error {
	if os.Getenv("GO_TYPESENSE_EXPORTER_ENABLE") != "1" {
		return errors.New("GO_TYPESENSE_EXPORTER_ENABLE=1 is required")
	}
	settings, err := loadExporterSettings()
	if err != nil {
		return err
	}
	redisOptions, err := redis.ParseURL(settings.RedisURL)
	if err != nil {
		return fmt.Errorf("invalid Redis metrics URL: %w", err)
	}
	redisOptions.DialTimeout = 2 * time.Second
	redisOptions.ReadTimeout = 2 * time.Second
	redisClient := redis.NewClient(redisOptions)
	defer redisClient.Close()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	conn, err := pgx.Connect(ctx, settings.DBURL)
	if err != nil {
		return errors.New("could not connect to local PostgreSQL")
	}
	defer conn.Close(context.Background())
	e := exporter{
		conn: conn, settings: settings,
		httpClient: &http.Client{Timeout: 10 * time.Second},
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	metrics := newExporterMetrics()
	e.metrics = metrics
	server := &http.Server{
		Addr:    fmt.Sprintf("127.0.0.1:%d", settings.MetricsPort),
		Handler: metrics.handler(), ReadHeaderTimeout: 5 * time.Second,
	}
	listener, err := net.Listen("tcp", server.Addr)
	if err != nil {
		return fmt.Errorf("bind exporter metrics listener: %w", err)
	}
	serverErrors := make(chan error, 1)
	go func() {
		serverErrors <- server.Serve(listener)
	}()
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	slog.Info("exporter.go_started", "target", "typesense")
	consecutiveFailures := 0
	lastLagCheck := time.Time{}
	lastRedisCheck := time.Time{}
	lastHealthCheck := time.Time{}
	for ctx.Err() == nil {
		select {
		case serverErr := <-serverErrors:
			return fmt.Errorf("exporter metrics listener: %w", serverErr)
		default:
		}
		if time.Since(lastRedisCheck) >= 15*time.Second {
			depths, err := sampleQueueDepths(ctx, redisClient)
			metrics.setRedis(depths, err == nil)
			if err != nil {
				slog.Warn("exporter.metrics_redis_error", "error", err)
			}
			lastRedisCheck = time.Now()
		}
		if time.Since(lastHealthCheck) >= 30*time.Second {
			healthy, err := probeTypesense(ctx, e.httpClient, settings.TypesenseURL, settings.OperationsKey)
			metrics.setTypesenseHealth(healthy && err == nil)
			if err != nil {
				slog.Warn("exporter.typesense_health_error", "error", err)
			}
			lastHealthCheck = time.Now()
		}
		start := time.Now()
		result, err := e.tick(ctx)
		if err != nil {
			if ctx.Err() != nil {
				break
			}
			if errors.Is(err, errNotExportOwner) {
				return err
			}
			if !errors.Is(err, errTypesenseImport) || conn.IsClosed() {
				return err
			}
			consecutiveFailures++
			backoff := 5 * time.Second
			for i := 1; i < consecutiveFailures && backoff < 60*time.Second; i++ {
				backoff *= 2
			}
			if backoff > 60*time.Second {
				backoff = 60 * time.Second
			}
			metrics.recordImportError(result.Read, result.ImportDuration, backoff)
			slog.Error("exporter.typesense_upsert_error", "error", err, "retry_in_s", backoff.Seconds())
			select {
			case <-ctx.Done():
				break
			case <-time.After(backoff):
			}
			continue
		}
		consecutiveFailures = 0
		metrics.recordTick(result, time.Since(start))
		if time.Since(lastLagCheck) >= 30*time.Second {
			var lag int64
			if err := conn.QueryRow(ctx,
				"SELECT count(*) FROM job_posting WHERE (updated_at, id) > ($1::timestamptz, $2::uuid)",
				result.Position.UpdatedAt, result.Position.ID,
			).Scan(&lag); err != nil {
				slog.Warn("exporter.metrics_typesense_lag_error", "error", err)
			} else {
				metrics.setLag(lag)
			}
			lastLagCheck = time.Now()
		}
		slog.Info("exporter.tick", "read", result.Read, "rejected", result.Rejected, "duration_s", time.Since(start).Seconds())
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(settings.Interval):
		}
	}
	return nil
}
