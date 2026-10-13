package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const b0EpochBarrier int64 = 7544422533504811009
const b0MaxEpoch int64 = 9999999999999

var errB0Epoch = errors.New("B0 routing epoch administration rejected")

func reserveB0Epoch(ctx context.Context, pool *pgxpool.Pool) (int64, error) {
	var epoch int64
	err := pgx.BeginFunc(ctx, pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", b0EpochBarrier); err != nil {
			return errB0Epoch
		}
		if err := tx.QueryRow(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq'::regclass)").Scan(&epoch); err != nil {
			return errB0Epoch
		}
		return nil
	})
	if err != nil || epoch < 1 || epoch > b0MaxEpoch {
		return 0, errB0Epoch
	}
	return epoch, nil
}

func attestB0Epoch(ctx context.Context, pool *pgxpool.Pool, expected int64) error {
	if expected < 1 || expected > b0MaxEpoch {
		return errB0Epoch
	}
	var last int64
	var called bool
	if err := pool.QueryRow(ctx, "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq").Scan(&last, &called); err != nil || !called || last < 1 || last > b0MaxEpoch || last != expected {
		return errB0Epoch
	}
	return nil
}

func writeB0Epoch(ctx context.Context, pool *pgxpool.Pool, command, rawExpected string, out io.Writer) error {
	if command == "reserve-epoch" {
		epoch, err := reserveB0Epoch(ctx, pool)
		if err != nil {
			return err
		}
		return json.NewEncoder(out).Encode(map[string]any{"routing_epoch": epoch})
	}
	if command != "attest-epoch" {
		return errB0Epoch
	}
	expected, err := strconv.ParseInt(rawExpected, 10, 64)
	if err != nil || strconv.FormatInt(expected, 10) != rawExpected || attestB0Epoch(ctx, pool, expected) != nil {
		return errB0Epoch
	}
	return json.NewEncoder(out).Encode(map[string]any{"routing_epoch": expected, "current": true})
}

func runB0Epoch(command string) error {
	if command != "reserve-epoch" && command != "attest-epoch" {
		return errB0Epoch
	}
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	role := os.Getenv("CRAWLER_DB_ROLE")
	if role == "" {
		role = "lightpanda-b0-activation"
	}
	// Reuse the existing one-connection administrative pool and the original
	// local pool's startup timeouts/keepalives. The selected one-off service
	// supplies the same LOCAL_DATABASE_URL principal as the Python command.
	dsn := os.Getenv("LOCAL_DATABASE_URL")
	if dsn == "" {
		return errB0Epoch
	}
	config, err := providerCutoverPoolConfig(dsn, role)
	if err != nil {
		return errB0Epoch
	}
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errB0Epoch
	}
	defer pool.Close()
	return writeB0Epoch(ctx, pool, command, os.Getenv("LIGHTPANDA_B0_ROUTING_EPOCH"), os.Stdout)
}
