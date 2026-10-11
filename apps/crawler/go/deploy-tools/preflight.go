package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

var preflightQueries = []struct{ name, query string }{
	{"sampler_age_seconds", `time() - max(jobseek_host_observability_last_collect_unixtime{host_role="postgresql"})`},
	{"ready", `max(jobseek_postgresql_ready{host_role="postgresql"})`},
	{"data_free_ratio", `max(node_filesystem_avail_bytes{job="integrations/unix",host_role="postgresql",fstype="xfs",mountpoint=~"/mnt/.*"}) / max(node_filesystem_size_bytes{job="integrations/unix",host_role="postgresql",fstype="xfs",mountpoint=~"/mnt/.*"})`},
	{"repository_free_ratio", `max(node_filesystem_avail_bytes{job="integrations/unix",host_role="postgresql",fstype="cifs",mountpoint=~"/mnt/.*"}) / max(node_filesystem_size_bytes{job="integrations/unix",host_role="postgresql",fstype="cifs",mountpoint=~"/mnt/.*"})`},
	{"emergency_reserve_bytes", `max(jobseek_postgresql_emergency_reserve_bytes{host_role="postgresql"})`},
	{"backup_attempt_success", `max(jobseek_backup_last_attempt_success{host_role="postgresql",service="postgresql"})`},
	{"backup_age_seconds", `time() - max(jobseek_backup_last_success_unixtime{host_role="postgresql",service="postgresql"})`},
	{"archive_failures_1h", `max(increase(jobseek_postgresql_archive_failed_total{host_role="postgresql"}[1h]))`},
}

func validatePreflight(values map[string]float64) error {
	for _, query := range preflightQueries {
		value, ok := values[query.name]
		if !ok || math.IsNaN(value) || math.IsInf(value, 0) {
			return errors.New("PostgreSQL telemetry missing or non-finite")
		}
	}
	switch {
	case values["sampler_age_seconds"] < 0 || values["sampler_age_seconds"] > 300:
		return errors.New("PostgreSQL host telemetry is stale")
	case values["ready"] != 1:
		return errors.New("PostgreSQL is not ready")
	case values["data_free_ratio"] < 0.15:
		return errors.New("PostgreSQL data Volume lacks deploy headroom")
	case values["repository_free_ratio"] < 0.20:
		return errors.New("PostgreSQL backup repository lacks deploy headroom")
	case values["emergency_reserve_bytes"] < 2147483648:
		return errors.New("PostgreSQL emergency recovery reserve is missing")
	case values["backup_attempt_success"] != 1:
		return errors.New("the latest PostgreSQL backup attempt failed")
	case values["backup_age_seconds"] < 0 || values["backup_age_seconds"] > 129600:
		return errors.New("the latest PostgreSQL backup is stale")
	case values["archive_failures_1h"] > 0:
		return errors.New("PostgreSQL recorded a recent WAL archive failure")
	}
	return nil
}

func postgresPreflight(ctx context.Context, remote, username, password string) error {
	if remote == "" || username == "" || password == "" {
		return errors.New("Grafana URL, username, and password are required")
	}
	u, err := url.Parse(strings.TrimRight(strings.TrimSpace(remote), "/"))
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || !strings.HasSuffix(u.Path, "/api/prom/push") {
		return errors.New("Grafana remote-write URL has an unexpected shape")
	}
	u.Path = strings.TrimSuffix(u.Path, "/push") + "/api/v1/query"
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	values := map[string]float64{}
	for _, query := range preflightQueries {
		u.RawQuery = url.Values{"query": []string{query.query}}.Encode()
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
		if err != nil {
			return errors.New("PostgreSQL telemetry request invalid")
		}
		request.SetBasicAuth(username, password)
		response, err := client.Do(request)
		if err != nil {
			return errors.New("PostgreSQL telemetry request failed")
		}
		body, readErr := io.ReadAll(io.LimitReader(response.Body, (1<<20)+1))
		response.Body.Close()
		if response.StatusCode != 200 || readErr != nil || len(body) > 1<<20 {
			return errors.New("PostgreSQL telemetry response failed")
		}
		var result struct {
			Status string
			Data   struct {
				Result []struct{ Value []json.RawMessage }
			}
		}
		if json.Unmarshal(body, &result) != nil || result.Status != "success" || len(result.Data.Result) != 1 || len(result.Data.Result[0].Value) != 2 {
			return errors.New("PostgreSQL telemetry did not return exactly one scalar")
		}
		raw := result.Data.Result[0].Value[1]
		var text string
		if json.Unmarshal(raw, &text) != nil {
			text = string(raw)
		}
		value, err := strconv.ParseFloat(text, 64)
		if err != nil {
			return errors.New("PostgreSQL telemetry scalar invalid")
		}
		values[query.name] = value
	}
	return validatePreflight(values)
}
