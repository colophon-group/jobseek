package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

type reconciliationOptions struct {
	Repair, Full, Fresh           bool
	MaxPartitions, StartPartition int
	Benchmark                     string
}

func reconciliationErrorClass(err error) string {
	var postgres *pgconn.PgError
	if errors.As(err, &postgres) {
		return "PostgreSQL_" + postgres.Code
	}
	var transport *url.Error
	if errors.As(err, &transport) {
		return "TransportError"
	}
	var acquisition *reconciliationAcquisitionError
	if errors.As(err, &acquisition) {
		return "TypesenseExportError"
	}
	if errors.Is(err, context.Canceled) {
		return "InterruptedRun"
	}
	return "ReconciliationError"
}

func parseReconciliationOptions(args []string) (reconciliationOptions, error) {
	options := reconciliationOptions{}
	flags := flag.NewFlagSet("reconcile", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	flags.BoolVar(&options.Repair, "repair", false, "apply and verify repairs")
	flags.BoolVar(&options.Full, "full", false, "finish the remaining partition cycle")
	flags.BoolVar(&options.Fresh, "fresh-cycle", false, "start a new full repair proof")
	flags.IntVar(&options.MaxPartitions, "max-partitions", 16, "bounded partition count")
	flags.IntVar(&options.StartPartition, "start-partition", 0, "read-only starting partition")
	flags.StringVar(&options.Benchmark, "candidate-order-benchmark-sha256", "", "readiness benchmark digest")
	target := flags.String("target", "typesense", "Typesense only")
	if err := flags.Parse(args); err != nil {
		return options, err
	}
	if flags.NArg() != 0 || *target != "typesense" {
		return options, errors.New("reconcile accepts only Typesense and named options")
	}
	if options.MaxPartitions < 1 || options.MaxPartitions > 256 {
		return options, errors.New("max-partitions must be 1..256")
	}
	if _, _, err := reconciliationBounds(options.StartPartition); err != nil {
		return options, err
	}
	if options.Fresh && !(options.Repair && options.Full) {
		return options, errors.New("fresh-cycle requires repair and full")
	}
	if options.Benchmark != "" && (!(options.Repair && options.Full && options.Fresh) || !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(options.Benchmark)) {
		return options, errors.New("readiness receipt requires a lowercase SHA-256 and a fresh full repair")
	}
	return options, nil
}

func runReconciliation(ctx context.Context, d *reconciliationDatabase, index reconciliationIndex, options reconciliationOptions, refreshMaps func(context.Context) error) (summary reconciliationSummary, resultErr error) {
	id, err := reconciliationRunID()
	if err != nil {
		return summary, err
	}
	mode := "dry-run"
	if options.Repair {
		mode = "repair"
	}
	summary = reconciliationSummary{RunID: id, Mode: mode, Target: "typesense"}
	var acquired bool
	if err := d.conn.QueryRow(ctx, `SELECT pg_try_advisory_lock($1::bigint)`, reconciliationLockID).Scan(&acquired); err != nil {
		closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = d.conn.Close(closeCtx) // A lost reply may still have acquired the lock.
		return summary, err
	}
	if !acquired {
		slog.Info("reconciliation.already_running")
		if options.Fresh {
			return summary, errors.New("fresh reconciliation proof could not acquire its advisory lock")
		}
		return summary, nil
	}
	defer func() {
		unlockCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		var released bool
		err := d.conn.QueryRow(unlockCtx, `SELECT pg_advisory_unlock($1::bigint)`, reconciliationLockID).Scan(&released)
		if err != nil || !released {
			closeCtx, closeCancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer closeCancel()
			_ = d.conn.Close(closeCtx)
			resultErr = errors.Join(resultErr, errors.New("reconciliation advisory lock release failed"))
		}
	}()
	if err := d.startRun(ctx, summary); err != nil {
		return summary, err
	}
	finished := false
	defer func() {
		if finished {
			return
		}
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		status, errorClass := "failed", reconciliationErrorClass(resultErr)
		if ctx.Err() != nil {
			status, errorClass = "interrupted", "InterruptedRun"
		}
		resultErr = errors.Join(resultErr, d.finishRun(cleanupCtx, summary, status, &errorClass))
	}()
	partition := options.StartPartition
	if options.Repair {
		partition, err = d.ensureCycle(ctx, options.Fresh)
		if err != nil {
			return summary, err
		}
	}
	budget := options.MaxPartitions
	if options.Full {
		budget = reconciliationPartitions - partition
	}
	var last reconciliationResult
	defer func() {
		if resultErr != nil && options.Repair && ctx.Err() == nil {
			cleanupCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			resultErr = errors.Join(resultErr, d.recordFailure(cleanupCtx, reconciliationErrorClass(resultErr), last.Unresolved))
		}
	}()
	for count := 0; count < budget; count++ {
		last = reconciliationResult{}
		attempts := 1
		if options.Repair {
			attempts = 3
		}
		for attempt := 0; attempt < attempts; attempt++ {
			if err := refreshMaps(ctx); err != nil {
				return summary, err
			}
			last, err = reconcilePartition(ctx, d, index, partition, options.Repair)
			if err != nil {
				if last.Unresolved != 0 {
					summary.add(last, false)
				}
				return summary, err
			}
			if last.Unresolved == 0 || attempt == attempts-1 {
				break
			}
			slog.Info("reconciliation.partition_retry", "target", "typesense", "partition", partition, "attempt", attempt+1, "unresolved", last.Unresolved)
		}
		if options.Repair && last.Unresolved != 0 {
			summary.add(last, false)
			return summary, errors.New("reconciliation partition left unresolved rows")
		}
		var bootstrap *bool
		if options.Repair && partition == reconciliationPartitions-1 {
			complete, err := d.bootstrapComplete(ctx)
			if err != nil {
				return summary, err
			}
			if !complete {
				started := time.Now()
				active, inactive, deleted, err := bootstrapReconciliation(ctx, d, index)
				if err != nil {
					return summary, err
				}
				last.RemoteRows += active + inactive
				last.RemoteActive += active
				last.RemoteOnlyActive += active
				last.RemoteOnlyInactive += inactive
				last.Detected += deleted
				last.Repaired += deleted
				last.Duration += time.Since(started).Seconds()
				complete = true
				bootstrap = &complete
			}
		}
		summary.add(last, true)
		complete := partition == reconciliationPartitions-1
		if options.Repair {
			complete, err = d.advance(ctx, last, bootstrap)
			if err != nil {
				return summary, err
			}
		}
		if err := d.persistRun(ctx, summary); err != nil {
			return summary, err
		}
		if complete {
			break
		}
		partition++
	}
	if options.Fresh && summary.Partitions != reconciliationPartitions {
		return summary, errors.New("fresh reconciliation proof did not inspect every partition")
	}
	if err := d.finishRun(ctx, summary, "success", nil); err != nil {
		return summary, err
	}
	finished = true
	slog.Info("reconciliation.completed", "run_id", summary.RunID, "mode", summary.Mode, "target_scope", summary.Target, "partitions", summary.Partitions,
		"checked_local", summary.Local, "checked_remote", summary.Remote, "detected", summary.Detected, "repaired", summary.Repaired, "unresolved", summary.Unresolved)
	return summary, nil
}

func reconciliationReadinessReceipt(ctx context.Context, d *reconciliationDatabase, summary reconciliationSummary, benchmark string) (string, error) {
	if summary.Mode != "repair" || summary.Target != "typesense" || summary.Partitions != 256 || summary.Unresolved != 0 || summary.Local < 0 || int64(summary.Local) > 1<<53-1 || !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(benchmark) {
		return "", errors.New("candidate-order receipt requires a fresh complete repair proof")
	}
	var completed *time.Time
	var status, mode, target string
	var partitions, local, unresolved int
	err := d.conn.QueryRow(ctx, `SELECT completed_at,status,mode,target_scope,partitions_completed,checked_local,unresolved FROM cross_store_reconciliation_run WHERE run_id=$1::uuid`, summary.RunID).Scan(&completed, &status, &mode, &target, &partitions, &local, &unresolved)
	if err != nil {
		return "", err
	}
	if completed == nil || status != "success" || mode != "repair" || target != "typesense" || partitions != 256 || local != summary.Local || unresolved != 0 {
		return "", errors.New("durable reconciliation ledger does not prove candidate-order readiness")
	}
	completedAt := completed.UTC().Format("2006-01-02T15:04:05.000000Z")
	if completed.Nanosecond() == 0 {
		completedAt = completed.UTC().Format(time.RFC3339)
	}
	payload := map[string]any{"authoritativeCount": local, "benchmarkSha256": benchmark, "completedAt": completedAt, "keyVersion": "uuid-int64-active-v1", "partitions": partitions, "reconciliationRunId": summary.RunID, "schemaVersion": "typesense-stable-candidate-order-readiness-v1", "unresolved": 0}
	raw, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(raw), nil
}

func runReconcile(args []string) error {
	options, err := parseReconciliationOptions(args)
	if err != nil {
		return err
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	settings, err := loadExporterSettings()
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	config, err := pgx.ParseConfig(settings.DBURL)
	if err != nil {
		return errors.New("invalid reconciliation database configuration")
	}
	role := os.Getenv("CRAWLER_DB_ROLE")
	if role != "maintenance" {
		role = "reconciliation"
	}
	config.RuntimeParams["application_name"] = "jobseek:crawler:" + role + ":local"
	config.RuntimeParams["statement_timeout"] = "5min"
	config.RuntimeParams["idle_in_transaction_session_timeout"] = "60s"
	config.RuntimeParams["tcp_keepalives_idle"] = "60"
	config.RuntimeParams["tcp_keepalives_interval"] = "10"
	config.RuntimeParams["tcp_keepalives_count"] = "3"
	conn, err := pgx.ConnectConfig(ctx, config)
	if err != nil {
		return errors.New("could not connect to reconciliation database")
	}
	defer func() {
		closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = conn.Close(closeCtx)
	}()
	client := &http.Client{Timeout: 300 * time.Second}
	defer client.CloseIdleConnections()
	database := &reconciliationDatabase{conn: conn}
	index := reconciliationHTTP{Client: client, BaseURL: settings.TypesenseURL, Key: settings.OperationsKey, RetryDelay: time.Second}
	summary, err := runReconciliation(ctx, database, index, options, func(ctx context.Context) error {
		maps, err := loadMaps(ctx, conn)
		if err == nil {
			database.maps = maps
		}
		return err
	})
	if err != nil {
		slog.Error("reconciliation.failed", "error_class", reconciliationErrorClass(err))
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return errors.New("Typesense reconciliation failed; inspect its durable run and partition state")
	}
	if options.Benchmark != "" {
		receipt, err := reconciliationReadinessReceipt(ctx, database, summary, options.Benchmark)
		if err != nil {
			return errors.New("candidate-order durable readiness proof failed")
		}
		encoded, _ := json.Marshal(map[string]string{"candidate_order_readiness_receipt": receipt})
		fmt.Println(string(encoded))
	}
	return nil
}
