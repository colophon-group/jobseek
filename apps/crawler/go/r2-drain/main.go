package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"math/rand"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	claimBatch     = 50
	bufferSize     = 200
	consumerCount  = 30
	reaperInterval = 5 * time.Minute
	staleAfter     = 10 * time.Minute
)

type settings struct {
	DBURL       string
	R2URL       string
	R2Bucket    string
	R2KeyID     string
	R2Secret    string
	MetricsPort int
	RetryBase   time.Duration
	RetryMax    time.Duration
}

func loadSettings() (settings, error) {
	c := settings{
		DBURL: os.Getenv("LOCAL_DATABASE_URL"), R2URL: os.Getenv("R2_ENDPOINT_URL"),
		R2Bucket: os.Getenv("R2_BUCKET"), R2KeyID: os.Getenv("R2_ACCESS_KEY_ID"),
		R2Secret: os.Getenv("R2_SECRET_ACCESS_KEY"), MetricsPort: 9094,
		RetryBase: 5 * time.Second, RetryMax: 900 * time.Second,
	}
	if c.DBURL == "" {
		return c, errors.New("LOCAL_DATABASE_URL is required")
	}
	if raw := os.Getenv("METRICS_PORT"); raw != "" {
		port, err := strconv.Atoi(raw)
		if err != nil || port < 1 || port > 65535 {
			return c, errors.New("METRICS_PORT must be a valid TCP port")
		}
		c.MetricsPort = port
	}
	for _, v := range []struct {
		name string
		dest *time.Duration
	}{{"DRAIN_RETRY_BASE_SECONDS", &c.RetryBase}, {"DRAIN_RETRY_MAX_SECONDS", &c.RetryMax}} {
		if raw := os.Getenv(v.name); raw != "" {
			seconds, err := strconv.ParseFloat(raw, 64)
			if err != nil || math.IsNaN(seconds) || math.IsInf(seconds, 0) || seconds <= 0 || seconds > 86400 {
				return c, fmt.Errorf("%s must be a positive bounded duration", v.name)
			}
			*v.dest = time.Duration(seconds * float64(time.Second))
		}
	}
	if c.RetryMax < c.RetryBase {
		return c, errors.New("DRAIN_RETRY_MAX_SECONDS must be at least DRAIN_RETRY_BASE_SECONDS")
	}
	return c, nil
}

func retryDelay(failureCount int32, base, maximum time.Duration) time.Duration {
	ceiling := base
	for i := int32(1); i < failureCount && ceiling < maximum; i++ {
		if ceiling >= maximum/2 {
			ceiling = maximum
			break
		}
		ceiling *= 2
	}
	if ceiling > maximum {
		ceiling = maximum
	}
	return ceiling/2 + time.Duration(rand.Int63n(int64(ceiling/2)+1))
}

func reason(err error) string {
	var p *putError
	if errors.As(err, &p) {
		if p.status >= 500 && p.status <= 599 {
			return "http_5xx"
		}
		if p.status != 0 {
			return "http_other"
		}
		var netErr net.Error
		if errors.As(p.err, &netErr) && netErr.Timeout() {
			return "timeout"
		}
		return "transport"
	}
	return "other"
}

func consume(ctx context.Context, store descriptionStore, r2 *r2Client, work <-chan description, c settings, m *metrics, log *slog.Logger) {
	for {
		select {
		case <-ctx.Done():
			return
		case d := <-work:
			started := time.Now()
			failureReason := "database"
			err := r2.put(ctx, d)
			if err != nil {
				failureReason = reason(err)
			}
			if err == nil {
				var current bool
				current, err = store.complete(ctx, d)
				if err == nil {
					m.observeUpload(time.Since(started), current, int64(len(d.HTML)))
					if current {
						log.Info("r2_drain.uploaded", "posting_id", d.PostingID, "locale", d.Locale)
					} else {
						log.Info("r2_drain.upload_superseded", "posting_id", d.PostingID, "locale", d.Locale, "hash", d.Hash)
					}
					continue
				}
			}
			if ctx.Err() != nil {
				// The next sole owner reaps this NULL claim on startup.
				return
			}
			failureCount := d.Failures + 1
			delay := retryDelay(failureCount, c.RetryBase, c.RetryMax)
			scheduled, scheduleErr := store.retry(ctx, d, failureCount, delay)
			if scheduleErr != nil {
				log.Error("r2_drain.retry_schedule_error", "posting_id", d.PostingID, "locale", d.Locale, "error", scheduleErr)
			}
			m.observeFailure(failureReason, delay, scheduled)
			log.Warn("r2_drain.consumer_error", "posting_id", d.PostingID, "locale", d.Locale,
				"error", err, "failure_count", failureCount, "retry_in_s", delay.Seconds(), "retry_scheduled", scheduled)
		}
	}
}

func run(ctx context.Context, c settings, log *slog.Logger) error {
	poolConfig, err := pgxpool.ParseConfig(c.DBURL)
	if err != nil {
		return fmt.Errorf("parse database URL: %w", err)
	}
	poolConfig.MinConns = 1
	poolConfig.MaxConns = 6 // Same cap as the Python drain Compose role.
	db, err := pgxpool.NewWithConfig(ctx, poolConfig)
	if err != nil {
		return fmt.Errorf("connect database: %w", err)
	}
	defer db.Close()
	if err := db.Ping(ctx); err != nil {
		return fmt.Errorf("ping database: %w", err)
	}
	store := descriptionStore{db: db}
	r2, err := newR2Client(c.R2URL, c.R2Bucket, c.R2KeyID, c.R2Secret)
	if err != nil {
		return err
	}
	// The previous owner has been quiesced before this process starts. Every
	// pre-existing NULL claim is therefore orphaned, including recent ones.
	for {
		count, err := store.reap(ctx, nil)
		if err != nil {
			return fmt.Errorf("startup reap: %w", err)
		}
		if count > 0 {
			log.Info("r2_drain.reaped_orphans", "count", count, "startup", true)
		}
		if count < 500 {
			break
		}
	}
	m := newMetrics()
	server := &http.Server{Addr: fmt.Sprintf("127.0.0.1:%d", c.MetricsPort), Handler: m.handler(), ReadHeaderTimeout: 5 * time.Second}
	serverErrors := make(chan error, 1)
	go func() { serverErrors <- server.ListenAndServe() }()
	defer func() {
		stop, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(stop)
	}()
	work := make(chan description, bufferSize)
	for i := 0; i < consumerCount; i++ {
		go consume(ctx, store, r2, work, c, m, log)
	}
	log.Info("r2_drain.started", "consumers", consumerCount, "buffer_size", bufferSize)
	reaper := time.NewTicker(reaperInterval)
	defer reaper.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case err := <-serverErrors:
			return fmt.Errorf("metrics server: %w", err)
		case <-reaper.C:
			count, err := store.reap(ctx, ptr(staleAfter))
			if err != nil {
				log.Error("r2_drain.reaper_error", "error", err)
			} else if count > 0 {
				m.addReaped(count)
				log.Info("r2_drain.reaped_orphans", "count", count, "startup", false)
			}
		default:
			capacity := cap(work) - len(work)
			if capacity < 1 {
				wait(ctx, 100*time.Millisecond)
				continue
			}
			limit := min(claimBatch, capacity)
			claimed, err := store.claim(ctx, limit)
			if err != nil {
				log.Error("r2_drain.producer_fetch_error", "error", err)
				wait(ctx, 2*time.Second)
				continue
			}
			if len(claimed) == 0 {
				wait(ctx, 2*time.Second)
				continue
			}
			for _, d := range claimed {
				select {
				case <-ctx.Done():
					return nil // startup reap by the next owner restores unsent claims.
				case work <- d:
				}
			}
		}
	}
}

func ptr[T any](value T) *T { return &value }

func wait(ctx context.Context, delay time.Duration) {
	select {
	case <-ctx.Done():
	case <-time.After(delay):
	}
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "--healthcheck" {
		port := os.Getenv("METRICS_PORT")
		if port == "" {
			port = "9094"
		}
		client := &http.Client{Timeout: 2 * time.Second}
		resp, err := client.Get("http://127.0.0.1:" + port + "/health")
		if err != nil || resp.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		_ = resp.Body.Close()
		return
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	c, err := loadSettings()
	if err != nil {
		log.Error("r2_drain.config_error", "error", err)
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, c, log); err != nil {
		log.Error("r2_drain.stopped_error", "error", err)
		os.Exit(1)
	}
	log.Info("r2_drain.stopped")
}
