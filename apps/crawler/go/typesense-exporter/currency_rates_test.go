package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestCurrencyRatesMatchOriginalDecimalAndXML(t *testing.T) {
	body, err := os.ReadFile("testdata/python_currency_rates.json")
	if err != nil {
		t.Fatal(err)
	}
	var reference struct {
		Source string `json:"source_revision"`
		Cases  []struct {
			Name, XML string
			Accepted  bool
			Snapshot  currencySnapshot
		}
	}
	if json.Unmarshal(body, &reference) != nil || reference.Source != "f20f5fe83b0ba1a700bbb1d3566b5fc25b5e5b77" || len(reference.Cases) != 34 {
		t.Fatal("original reference missing")
	}
	for _, c := range reference.Cases {
		t.Run(c.Name, func(t *testing.T) {
			got, err := parseECBDailyRates([]byte(c.XML))
			if (err == nil) != c.Accepted || err == nil && !reflect.DeepEqual(got, c.Snapshot) {
				t.Fatalf("original snapshot differs: got %+v err %v expected %+v accepted %v", got, err, c.Snapshot, c.Accepted)
			}
		})
	}
}

func TestCurrencyFetchUsesAcceptHeaderAndRejectsIncompleteEvidence(t *testing.T) {
	good := `<Envelope><Cube time="2026-07-06"><Cube currency="USD" rate="1.25"/></Cube></Envelope>`
	for _, c := range []struct {
		name   string
		status int
		body   string
		ok     bool
	}{{"valid", 200, good, true}, {"not-found", 404, good, false}, {"unavailable", 503, good, false}, {"malformed", 200, "<Envelope>", false}, {"oversized", 200, strings.Repeat(" ", 1<<20+1), false}} {
		t.Run(c.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "GET" || r.Header.Get("Accept") != "application/xml,text/xml;q=0.9" {
					t.Error("request changed")
				}
				w.WriteHeader(c.status)
				io.WriteString(w, c.body)
			}))
			defer server.Close()
			_, err := fetchCurrencyRates(context.Background(), server.Client(), server.URL)
			if (err == nil) != c.ok {
				t.Fatal("fetch admission differs", err)
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := fetchCurrencyRates(ctx, http.DefaultClient, "http://127.0.0.1:1"); err == nil {
		t.Fatal("canceled fetch admitted")
	}
}

func TestCurrencyPostgresAtomicUpsertPreservesOtherCurrencies(t *testing.T) {
	ctx, conn, observer := registryTestDatabase(t)
	if _, err := conn.Exec(ctx, `CREATE TABLE currency_rate(currency TEXT PRIMARY KEY,to_eur NUMERIC NOT NULL,updated_at TIMESTAMPTZ NOT NULL);INSERT INTO currency_rate VALUES ('USD',9,'2020-01-01'),('GBP',2,'2020-01-01')`); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 7, 6, 12, 0, 0, 0, time.UTC)
	snapshot := currencySnapshot{RateDate: "2026-07-06", Rates: []currencyRate{{"EUR", "1"}, {"USD", "0.800000000000"}}}
	if n, err := upsertCurrencyRates(ctx, conn, snapshot, now); err != nil || n != 2 {
		t.Fatal(n, err)
	}
	var value string
	var updated time.Time
	if err := observer.QueryRow(ctx, `SELECT to_eur::text,updated_at FROM currency_rate WHERE currency='USD'`).Scan(&value, &updated); err != nil || value != "0.800000000000" || !updated.Equal(now) {
		t.Fatal("upsert differs", value, updated, err)
	}
	if err := observer.QueryRow(ctx, `SELECT to_eur::text,updated_at FROM currency_rate WHERE currency='GBP'`).Scan(&value, &updated); err != nil || value != "2" || updated.Year() != 2020 {
		t.Fatal("unrelated currency changed", err)
	}
	snapshot.Rates = []currencyRate{{"USD", "4"}, {"JPY", "invalid"}}
	if _, err := upsertCurrencyRates(ctx, conn, snapshot, now.Add(time.Hour)); err == nil {
		t.Fatal("partial batch accepted")
	}
	if err := observer.QueryRow(ctx, `SELECT to_eur::text,updated_at FROM currency_rate WHERE currency='USD'`).Scan(&value, &updated); err != nil || value != "0.800000000000" || !updated.Equal(now) {
		t.Fatal("failed batch did not roll back", err)
	}
}

func TestCurrencyCompletionMetricsRetainBothStatusesAndSwallowGatewayFailure(t *testing.T) {
	for _, success := range []bool{true, false} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			body, _ := io.ReadAll(r.Body)
			want := "0"
			if success {
				want = "1"
			}
			if r.Method != "PUT" || r.URL.Path != "/metrics/job/crawler-cron/cron_job/refresh-currency-rates" || !strings.Contains(string(body), `crawler_cron_last_run_status{job="refresh-currency-rates"} `+want+"\n") {
				t.Error("completion gauge changed")
			}
			w.WriteHeader(503)
		}))
		pushCronMetrics(server.URL, "refresh-currency-rates", success)
		server.Close()
	}
}
