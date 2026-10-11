package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Preserve the Python command's optional completion gauges. An unavailable
// Pushgateway must not change the backfill result or disclose its URL in logs.
func pushBackfillMetrics(baseURL string, success bool) {
	pushCronMetrics(baseURL, "backfill-typesense", success)
}

func pushCronMetrics(baseURL, job string, success bool) {
	if job != "backfill-typesense" && job != "refresh-currency-rates" {
		return
	}
	if baseURL == "" {
		return
	}
	u, err := url.Parse(baseURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		slog.Warn("cron_metrics.push_failed", "event", "cron_metrics.push_failed", "job", job, "reason", "invalid_gateway")
		return
	}
	u.Path = strings.TrimSuffix(u.Path, "/") + "/metrics/job/crawler-cron/cron_job/" + job
	status := 0
	if success {
		status = 1
	}
	body := fmt.Sprintf("# TYPE crawler_cron_last_run_ts gauge\ncrawler_cron_last_run_ts{job=\"%s\"} %d\n# TYPE crawler_cron_last_run_status gauge\ncrawler_cron_last_run_status{job=\"%s\"} %d\n", job, time.Now().Unix(), job, status)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPut, u.String(), strings.NewReader(body))
	if err == nil {
		req.Header.Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		client := &http.Client{Timeout: 5 * time.Second}
		defer client.CloseIdleConnections()
		var response *http.Response
		response, err = client.Do(req)
		if err == nil {
			_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
			_ = response.Body.Close()
			if response.StatusCode >= 200 && response.StatusCode < 300 {
				return
			}
		}
	}
	slog.Warn("cron_metrics.push_failed", "event", "cron_metrics.push_failed", "job", job, "reason", "request_failed")
}
