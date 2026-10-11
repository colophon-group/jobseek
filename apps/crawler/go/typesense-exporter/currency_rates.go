package main

import (
	"bytes"
	"context"
	"encoding/json"
	"encoding/xml"
	"errors"
	"io"
	"log/slog"
	"math/big"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
)

const ecbDailyURL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

type currencyRate struct {
	Currency string `json:"currency"`
	ToEUR    string `json:"to_eur"`
}
type currencySnapshot struct {
	RateDate string         `json:"rate_date"`
	Rates    []currencyRate `json:"rates"`
}

var currencyCode = regexp.MustCompile(`^[A-Z]{3}$`)
var decimalRate = regexp.MustCompile(`^[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$`)

// Decimal division in the original uses 28 significant digits followed by
// half-even quantization to 12 decimal places. Avoid binary floating point.
func roundedDecimal(r *big.Rat, places int) *big.Rat {
	unit := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(max(places, -places))), nil)
	n, d := new(big.Int).Set(r.Num()), new(big.Int).Set(r.Denom())
	if places >= 0 {
		n.Mul(n, unit)
	} else {
		d.Mul(d, unit)
	}
	q, rem := new(big.Int), new(big.Int)
	q.QuoRem(n, d, rem)
	cmp := new(big.Int).Lsh(rem, 1).Cmp(d)
	if cmp > 0 || cmp == 0 && q.Bit(0) == 1 {
		q.Add(q, big.NewInt(1))
	}
	if places >= 0 {
		return new(big.Rat).SetFrac(q, unit)
	}
	return new(big.Rat).SetInt(new(big.Int).Mul(q, unit))
}

func inverseCurrencyRate(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if len(raw) > 512 || !decimalRate.MatchString(raw) {
		return "", errors.New("invalid ECB rate")
	}
	if i := strings.IndexAny(raw, "eE"); i >= 0 {
		exponent, err := strconv.Atoi(raw[i+1:])
		if err != nil || exponent < -4096 || exponent > 4096 {
			return "", errors.New("invalid ECB rate exponent")
		}
	}
	r, ok := new(big.Rat).SetString(raw)
	if !ok || r.Sign() <= 0 || r.Num().BitLen() > 16384 || r.Denom().BitLen() > 16384 {
		return "", errors.New("invalid ECB rate")
	}
	r.Inv(r)
	order := len(r.Num().String()) - len(r.Denom().String())
	threshold := new(big.Rat).SetInt(new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(max(order, -order))), nil))
	if order < 0 {
		threshold.Inv(threshold)
	}
	if r.Cmp(threshold) < 0 {
		order--
	}
	r = roundedDecimal(r, 27-order)
	r = roundedDecimal(r, 12)
	value := r.FloatString(12)
	if len(strings.ReplaceAll(strings.TrimLeft(value, "0."), ".", "")) > 28 {
		return "", errors.New("ECB rate exceeds decimal precision")
	}
	return value, nil
}

func parseECBDailyRates(body []byte) (currencySnapshot, error) {
	fail := func() (currencySnapshot, error) { return currencySnapshot{}, errors.New("invalid ECB daily payload") }
	d := xml.NewDecoder(bytes.NewReader(body))
	depth, roots, dated, datedDepth := 0, 0, 0, -1
	date := ""
	rates := map[string]string{"EUR": "1"}
	for {
		token, err := d.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return fail()
		}
		switch t := token.(type) {
		case xml.Directive:
			return fail()
		case xml.CharData:
			if depth == 0 && strings.TrimSpace(string(t)) != "" {
				return fail()
			}
		case xml.StartElement:
			if depth == 0 {
				roots++
			}
			depth++
			attrs := map[string]string{}
			for _, a := range t.Attr {
				if _, exists := attrs[a.Name.Local]; exists {
					return fail()
				}
				attrs[a.Name.Local] = a.Value
			}
			if value, exists := attrs["time"]; exists {
				dated++
				datedDepth = depth
				date = value
				if _, err := time.Parse("2006-01-02", value); err != nil {
					return fail()
				}
			} else if datedDepth >= 0 && depth == datedDepth+1 {
				currency, rate := attrs["currency"], attrs["rate"]
				_, present := attrs["rate"]
				if currency == "" && !present {
					continue
				}
				if !currencyCode.MatchString(currency) || !present {
					return fail()
				}
				value, err := inverseCurrencyRate(rate)
				if err != nil {
					return fail()
				}
				rates[currency] = value
			}
		case xml.EndElement:
			if depth == datedDepth {
				datedDepth = -1
			}
			depth--
		}
	}
	if roots != 1 || depth != 0 || dated != 1 || len(rates) == 1 {
		return fail()
	}
	keys := make([]string, 0, len(rates))
	for key := range rates {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	result := currencySnapshot{RateDate: date}
	for _, key := range keys {
		result.Rates = append(result.Rates, currencyRate{key, rates[key]})
	}
	return result, nil
}

func fetchCurrencyRates(ctx context.Context, client *http.Client, endpoint string) (currencySnapshot, error) {
	r, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return currencySnapshot{}, errors.New("ECB request configuration failed")
	}
	r.Header.Set("Accept", "application/xml,text/xml;q=0.9")
	response, err := client.Do(r)
	if err != nil {
		return currencySnapshot{}, errors.New("ECB request failed")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return currencySnapshot{}, errors.New("ECB response failed")
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20+1))
	if err != nil || len(body) > 1<<20 {
		return currencySnapshot{}, errors.New("ECB payload unavailable or oversized")
	}
	return parseECBDailyRates(body)
}

func upsertCurrencyRates(ctx context.Context, conn *pgx.Conn, snapshot currencySnapshot, now time.Time) (int, error) {
	tx, err := conn.Begin(ctx)
	if err != nil {
		return 0, errors.New("currency transaction unavailable")
	}
	defer tx.Rollback(context.Background())
	batch := new(pgx.Batch)
	for _, r := range snapshot.Rates {
		batch.Queue(`INSERT INTO currency_rate (currency,to_eur,updated_at) VALUES ($1,$2::numeric,$3) ON CONFLICT (currency) DO UPDATE SET to_eur=EXCLUDED.to_eur,updated_at=EXCLUDED.updated_at`, r.Currency, r.ToEUR, now)
	}
	result := tx.SendBatch(ctx, batch)
	if result.Close() != nil {
		return 0, errors.New("currency update failed")
	}
	if tx.Commit(ctx) != nil {
		return 0, errors.New("currency commit failed")
	}
	return len(snapshot.Rates), nil
}

func runCurrencyRefresh(args []string) (resultErr error) {
	dry := len(args) == 1 && args[0] == "--dry-run"
	if len(args) > 0 && !dry {
		return errors.New("usage: --refresh-currency-rates [--dry-run]")
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	started := time.Now()
	slog.Info("cron.start", "event", "cron.start", "job", "refresh-currency-rates")
	defer func() {
		status := "success"
		if resultErr != nil {
			status = "failure"
		}
		slog.Info("cron.complete", "event", "cron.complete", "job", "refresh-currency-rates", "status", status, "duration_s", time.Since(started).Seconds())
		pushCronMetrics(os.Getenv("CRAWLER_PUSHGATEWAY_URL"), "refresh-currency-rates", resultErr == nil)
	}()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	client := &http.Client{Timeout: 60 * time.Second}
	defer client.CloseIdleConnections()
	snapshot, err := fetchCurrencyRates(ctx, client, ecbDailyURL)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	if !dry {
		role := os.Getenv("CRAWLER_DB_ROLE")
		if role == "" {
			role = "currency-refresh"
		}
		config, err := providerCutoverPoolConfig(os.Getenv("LOCAL_DATABASE_URL"), role)
		if err != nil {
			return err
		}
		conn, err := pgx.ConnectConfig(ctx, config.ConnConfig)
		if err != nil {
			return errors.New("currency connection unavailable")
		}
		defer conn.Close(context.Background())
		if _, err = upsertCurrencyRates(ctx, conn, snapshot, now); err != nil {
			return err
		}
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"rate_date": snapshot.RateDate, "count": len(snapshot.Rates), "updated_at": now, "dry_run": dry})
}
