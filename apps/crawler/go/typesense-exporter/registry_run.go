package main

import (
	"context"
	"errors"
	"flag"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type registryTransactionConnection interface {
	Begin(context.Context) (pgx.Tx, error)
}

func loadRegistryTables(directory string) (map[string]registryTable, error) {
	tables := map[string]registryTable{}
	for _, name := range []string{"occupation_domains", "occupations", "seniority", "technologies", "industries", "companies", "company_descriptions", "boards"} {
		table, err := loadRegistryCSV(directory, name, name != "companies" && name != "boards")
		if err != nil {
			return nil, err
		}
		tables[name] = table
	}
	return tables, nil
}

// Database transaction only; callers receive effects after an acknowledged
// commit. A failed or ambiguous commit never authorizes downstream publication.
func commitRegistry(ctx context.Context, conn registryTransactionConnection, tables map[string]registryTable, plan registryPlan, boards []registryBoard) (boardSyncInput, error) {
	var empty boardSyncInput
	tx, err := conn.Begin(ctx)
	if err != nil {
		return empty, err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = tx.Rollback(cleanup)
	}()
	if _, err = tx.Exec(ctx, "SET LOCAL lock_timeout = '30s'"); err != nil {
		return empty, err
	}
	if err = executeRegistryPlan(ctx, tx, plan); err != nil {
		return empty, err
	}
	effects, err := stageRegistryBoards(ctx, tx, boards, time.Now)
	if err != nil {
		return empty, err
	}
	count, err := resolveRegistryMisses(ctx, tx, tables)
	if err != nil {
		return empty, err
	}
	if err = tx.Commit(ctx); err != nil {
		return empty, err
	}
	slog.Info("sync.registry.local_committed", "engine", "go", "boards", len(effects.Schedules), "resolved_misses", count)
	return effects, nil
}

func runRegistry(args []string) error {
	flags := flag.NewFlagSet("sync-registry", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	source := flags.String("source-data-dir", "", "verified checkout CSV directory")
	dry := flags.Bool("dry-run", false, "prepare without database or queue writes")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 {
		return errors.New("usage: --sync-registry [--source-data-dir DIR] [--dry-run]")
	}
	directory, err := registryDataDirectory(*source)
	if err != nil {
		return err
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	tables, err := loadRegistryTables(directory)
	if err != nil {
		return err
	}
	if len(tables["companies"].Rows) == 0 && len(tables["boards"].Rows) == 0 {
		slog.Info("sync.empty")
		return nil
	}
	plan, err := registryTaxonomyPlan(tables)
	if err != nil {
		return err
	}
	companies, err := registryCompanyPlan(tables["companies"], tables["company_descriptions"])
	if err != nil {
		return err
	}
	plan = append(plan, companies...)
	boards, err := prepareRegistryBoards(tables["boards"])
	if err != nil {
		return err
	}
	if *dry {
		slog.Info("sync.registry.dry_run", "engine", "go", "companies", len(tables["companies"].Rows), "boards", len(boards), "statements", len(plan))
		return nil
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 45*time.Minute)
	defer cancel()
	dsn := os.Getenv("LOCAL_DATABASE_URL")
	if dsn == "" {
		return errors.New("LOCAL_DATABASE_URL is required")
	}
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return errors.New("invalid local registry database configuration")
	}
	config.MinConns = 0
	config.MaxConns = 1
	config.ConnConfig.ConnectTimeout = 15 * time.Second
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:deploy-sync|registry:local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "30000"
	config.ConnConfig.RuntimeParams["idle_in_transaction_session_timeout"] = "60000"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errors.New("registry database unavailable")
	}
	defer pool.Close()
	conn, err := pool.Acquire(ctx)
	if err != nil {
		return errors.New("registry connection unavailable")
	}
	defer conn.Release()
	if _, err = ensureRegistryLocationIndex(ctx, conn.Conn()); err != nil {
		return err
	}
	var before renameNameMaps
	if os.Getenv("TYPESENSE_OPERATIONS_KEY") != "" {
		snapshot, beginErr := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
		if beginErr != nil {
			return beginErr
		}
		before, err = loadRenameNames(ctx, snapshot)
		if err == nil {
			err = snapshot.Commit(ctx)
		} else {
			_ = snapshot.Rollback(ctx)
		}
		if err != nil {
			return err
		}
	}
	effects, err := commitRegistry(ctx, conn, tables, plan, boards)
	if err != nil {
		return err
	}
	conn.Release()
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379/0"
	}
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		return errors.New("invalid registry Redis configuration")
	}
	options.Protocol = 2
	options.DialTimeout = 3 * time.Second
	options.ReadTimeout = 30 * time.Second
	options.WriteTimeout = 30 * time.Second
	options.ContextTimeoutEnabled = true
	options.MaxRetries = -1
	client := redis.NewClient(options)
	defer client.Close()
	delays, err := loadDeadletterDelays()
	if err != nil {
		return err
	}
	queueCtx, queueCancel := context.WithTimeout(ctx, 5*time.Minute)
	err = applyBoardSync(queueCtx, client, effects, delays, time.Now)
	queueCancel()
	if err != nil {
		return err
	}
	slog.Info("sync.boards.redis_applied", "engine", "go", "redis_enqueued", len(effects.Schedules), "redis_orphans_removed", len(effects.Orphans))
	classifyCtx, classifyCancel := context.WithTimeout(ctx, 45*time.Second)
	report, err := resolveDeadletters(classifyCtx, pool, client, deadletterOptions{Action: "inspect"}, delays)
	classifyCancel()
	if err != nil {
		return err
	}
	slog.Info("sync.deadletters.reconciled", "engine", "go", "total", report.Total, "counts", report.Counts)
	pool.Close()
	if before != nil {
		if err = syncTaxonomiesContext(ctx, before); err != nil {
			return err
		}
	}
	slog.Info("sync.complete", "engine", "go", "companies", len(tables["companies"].Rows), "boards", len(boards))
	return ctx.Err()
}
