package queue

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type richWriteLatencyConn struct {
	net.Conn
	reads *atomic.Int64
}

func (c *richWriteLatencyConn) Read(p []byte) (int, error) {
	// Existing production PostgreSQL sockets were observed between 0.36ms
	// and 6.90ms. Model the high end, without changing the runtime deadline.
	time.Sleep(6 * time.Millisecond)
	c.reads.Add(1)
	return c.Conn.Read(p)
}

// One slow 500-row write must not strand other boards behind the fixed pool.
// All claims share the actual five-connection authority and 15-second budget.
// This is a controlled latency-envelope regression, not a production speedup
// measurement; canonical effects and whole-chunk rollback are checked as well.
func TestRealRichMonitorConcurrentBatchDeadline(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	f.client.settings.DefaultDelaySeconds = 0
	if err := f.client.redis.Set(ctx, "delay:greenhouse", "0", 0).Err(); err != nil {
		t.Fatal("private shared throttle setup failed")
	}
	const workers, size = 15, 500
	ids := []string{f.task.ID}
	config, err := f.client.redis.HGetAll(ctx, "board:"+f.task.ID).Result()
	if err != nil {
		t.Fatal("private board snapshot unavailable")
	}
	for i := 1; i < workers; i++ {
		id := ordinaryID(t)
		slug := fmt.Sprintf("large-write-%d", i)
		boardURL := "https://job-boards.greenhouse.io/" + slug
		metadata := `{"token":"` + slug + `","scraper_type":"skip"}`
		if _, err := f.observer.Exec(ctx, `INSERT INTO job_board
 (id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,
  scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser,
  next_check_at,is_enabled,board_status)
 SELECT $1::uuid,company_id,$2,$3,crawler_type,$4::jsonb,check_interval_minutes,
  scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser,
  clock_timestamp()-interval '1 minute',true,'active'
 FROM job_board WHERE id=$5::uuid`, id, slug, boardURL, metadata, f.task.ID); err != nil {
			t.Fatal("private board creation failed")
		}
		copy := cloneConfig(config)
		copy["board_slug"], copy["board_url"], copy["metadata"] = slug, boardURL, metadata
		if err := f.client.redis.HSet(ctx, "board:"+id, copy).Err(); err != nil {
			t.Fatal("private board cache setup failed")
		}
		if err := f.client.redis.ZAdd(ctx, "monitors_simple:greenhouse", redis.Z{Score: 1, Member: id}).Err(); err != nil {
			t.Fatal("private monitor queue setup failed")
		}
		ids = append(ids, id)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM ordinary_worker_write_fence WHERE board_id=ANY($1::uuid[])", ids)
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE company_id=$1::uuid AND id<>$2::uuid", f.company, f.task.ID)
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=ANY($1::uuid[])", ids[1:])
	})
	a, err := OpenDefaultAuthority(ctx, f.dsn, f.client, f.epoch)
	if err != nil {
		t.Fatal("private default authority unavailable")
	}
	t.Cleanup(a.Close)
	claims := make([]*Claim, workers)
	batches := make([][]GreenhouseRichPosting, workers)
	body := "<p>" + strings.Repeat("Build reliable services with Go and SQL. ", 50) + "</p>"
	for i := range workers {
		claims[i], err = a.Claim(ctx, Simple)
		if err != nil || claims[i] == nil {
			t.Fatal("private monitor claim unavailable")
		}
		for j := range size {
			url := fmt.Sprintf("https://job-boards.greenhouse.io/fixture-%s/jobs/%d", claims[i].boardID, j)
			batches[i] = append(batches[i], richPosting(t, url, "Baseline", body))
		}
		result, err := a.WriteGreenhouseRichBatch(ctx, claims[i], batches[i])
		if err != nil || result == nil || result.Inserted != size {
			t.Fatal("private full-batch baseline creation failed")
		}
		for j := range size {
			batches[i][j].Content.Fields.Titles = []string{"Refreshed"}
			batches[i][j].Content.Description, _ = richDescription(body+"<p>Updated.</p>", "en")
		}
	}
	if _, err := f.observer.Exec(ctx, "UPDATE descriptions SET r2_uploaded=true WHERE posting_id IN (SELECT id FROM job_posting WHERE company_id=$1::uuid)", f.company); err != nil {
		t.Fatal("private completed-upload baseline unavailable")
	}
	// Only the fixture transport changes. Pool size, SQL timeouts, execution
	// mode, ownership, leases and all production writer code stay unchanged.
	cfg := a.pool.Config()
	var reads atomic.Int64
	dial := cfg.ConnConfig.DialFunc
	cfg.ConnConfig.DialFunc = func(ctx context.Context, network, address string) (net.Conn, error) {
		conn, err := dial(ctx, network, address)
		if err != nil {
			return nil, err
		}
		return &richWriteLatencyConn{Conn: conn, reads: &reads}, nil
	}
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal("private timed writer pool unavailable")
	}
	a.pool.Close()
	a.pool = pool
	warm := make([]*pgxpool.Conn, cfg.MaxConns)
	for i := range warm {
		warm[i], err = pool.Acquire(ctx)
		if err != nil {
			t.Fatal("private timed pool warmup failed")
		}
	}
	for _, conn := range warm {
		conn.Release()
	}
	prior := runtime.GOMAXPROCS(2)
	defer runtime.GOMAXPROCS(prior)
	readStart := reads.Load()
	start := time.Now()
	type outcome struct {
		index int
		ok    bool
	}
	results := make(chan outcome, workers)
	for i := range workers {
		go func() {
			result, err := a.WriteGreenhouseRichBatch(ctx, claims[i], batches[i])
			results <- outcome{i, err == nil && result != nil && result.Touched == size}
		}()
	}
	succeeded := 0
	for range workers {
		result := <-results
		var refreshed, unchanged, total int
		if err := f.observer.QueryRow(ctx, `SELECT count(*),
 count(*) FILTER (WHERE p.titles=ARRAY['Refreshed'] AND d.html=$2 AND d.hash=$3 AND NOT d.r2_uploaded),
 count(*) FILTER (WHERE p.titles=ARRAY['Baseline'] AND d.html=$4 AND d.r2_uploaded)
 FROM job_posting p JOIN descriptions d ON d.posting_id=p.id AND d.locale='en'
 WHERE p.board_id=$1::uuid AND p.id<>$1::uuid`, claims[result.index].boardID,
			batches[result.index][0].Content.Description.HTML, batches[result.index][0].Content.Description.Hash, body).Scan(&total, &refreshed, &unchanged); err != nil {
			t.Fatal("private batch-effect readback failed")
		}
		if total != size || result.ok && refreshed != size || !result.ok && unchanged != size {
			t.Error("writer lost complete-chunk effects or rollback")
		}
		if result.ok {
			succeeded++
		}
	}
	measurement, _ := json.Marshal(map[string]any{"workers": workers, "batch_size": size, "pool_max": cfg.MaxConns, "read_delay_ms": 6, "elapsed_ms": time.Since(start).Milliseconds(), "socket_reads": reads.Load() - readStart, "succeeded": succeeded, "failed": workers - succeeded})
	t.Log("native_write_measurement " + string(measurement))
	if succeeded != workers {
		t.Errorf("only %d of %d full monitor chunks completed inside the unchanged deadline", succeeded, workers)
	}
}
