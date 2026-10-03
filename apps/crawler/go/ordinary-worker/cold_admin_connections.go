package worker

import (
	"context"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type coldAdminConnections struct {
	pool   *pgxpool.Pool
	client *queue.Client
}

// RunColdAdminInHostScope uses the coordinator's exact live SQL scope and
// borrowed connections. The host verifies the Redis endpoint. It never opens
// caller endpoints, closes borrowed
// resources, shells out or treats a past host receipt as continuing authority.
// The host still owns retained phase outputs and complete host/Redis exclusion.
func RunColdAdminInHostScope(ctx context.Context, c ColdAdminConfig, pool *pgxpool.Pool, client *queue.Client) (*ColdAdminIdentity, error) {
	if queue.CheckHostColdSQLScope(ctx, pool, c.source) != nil {
		return nil, ErrStartup
	}
	result, err := runColdAdmin(ctx, c, &coldAdminConnections{pool, client})
	if err != nil || queue.CheckHostColdSQLScope(ctx, pool, c.source) != nil {
		return nil, ErrStartup
	}
	return result, nil
}

func acquireColdAdminConnections(ctx context.Context, c ColdAdminConfig, borrowed *coldAdminConnections, needRedis bool) (*coldAdminConnections, func(), error) {
	if borrowed != nil {
		if queue.CheckHostColdSQLScope(ctx, borrowed.pool, c.source) != nil || needRedis && borrowed.client == nil {
			return nil, nil, ErrStartup
		}
		return borrowed, func() {}, nil
	}
	config, err := pgxpool.ParseConfig(c.database)
	if err != nil {
		return nil, nil, ErrStartup
	}
	config.MinConns, config.MaxConns = 0, 1
	config.MaxConnIdleTime = time.Minute
	config.ConnConfig.ConnectTimeout = 3 * time.Second
	config.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeDescribeExec
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:ordinary-cold-coordinator:local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "10s"
	config.ConnConfig.RuntimeParams["idle_in_transaction_session_timeout"] = "15s"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, nil, ErrStartup
	}
	connections := &coldAdminConnections{pool: pool}
	close := func() {
		if connections.client != nil {
			_ = connections.client.Close()
		}
		pool.Close()
	}
	if needRedis {
		connections.client, err = queue.Open(c.redis, queue.Settings{LeaseTTL: 600 * time.Second, MaxDomains: 10})
		if err != nil {
			close()
			return nil, nil, ErrStartup
		}
	}
	return connections, close, nil
}
