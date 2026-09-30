package executor

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"time"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

// Run installs one process-local native owner. Callers must exit the process
// on an error: shutdown-grace failure intentionally skips blocking resource
// cleanup so an uncooperative handler cannot prevent termination.
func Run(ctx context.Context, config RuntimeConfig, dataDirectory string) error {
	if config.Shard != "lightpanda-b0" || config.Epoch < 1 || config.Epoch > maxIdentityInteger || config.databaseURL == "" {
		return ErrProtocol
	}
	startup, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	store, err := OpenStore(startup, config.databaseURL)
	if err != nil {
		return errors.New("executor database startup failed")
	}
	if err := store.AttestEpoch(startup, config.Epoch); err != nil {
		store.Close()
		return errors.New("executor startup route rejected")
	}
	matcher, err := enrichment.Load(dataDirectory)
	if err != nil {
		store.Close()
		return errors.New("executor taxonomy startup failed")
	}
	lookups, err := LoadLookups(startup, store)
	if err != nil {
		store.Close()
		return errors.New("executor lookup startup failed")
	}
	locations, err := LoadLocations(startup, store)
	if err != nil {
		store.Close()
		return errors.New("executor location startup failed")
	}
	owner := &Executor{Store: store, Processor: &Processor{Matcher: matcher, Lookups: lookups, Locations: locations}, Shard: config.Shard, Epoch: config.Epoch}
	err = (Server{Store: store, Task: owner.Handle, Shard: config.Shard, Epoch: config.Epoch}).Serve(ctx)
	if err != nil {
		return errors.New("executor server failed")
	}
	closeError := locations.Close()
	store.Close()
	return closeError
}

// CheckHealth uses the reserved route conversation, without opening an
// additional PostgreSQL connection or loading another taxonomy/model snapshot.
func CheckHealth(ctx context.Context, config RuntimeConfig) error {
	return checkHealth(ctx, config, SocketPath)
}

func checkHealth(ctx context.Context, config RuntimeConfig, path string) error {
	if config.Shard != "lightpanda-b0" || config.Epoch < 1 || config.Epoch > maxIdentityInteger {
		return ErrProtocol
	}
	if err := privateSocketDirectory(path); err != nil {
		return err
	}
	if err := privateSocket(path); err != nil {
		return err
	}
	conn, err := (&net.Dialer{Timeout: time.Second}).DialContext(ctx, "unix", path)
	if err != nil {
		return errors.New("executor health unavailable")
	}
	defer conn.Close()
	deadline := time.Now().Add(3 * time.Second)
	if bound, ok := ctx.Deadline(); ok && bound.Before(deadline) {
		deadline = bound
	}
	if err := conn.SetDeadline(deadline); err != nil {
		return err
	}
	if err := WriteMessage(conn, map[string]any{"version": Protocol, "type": "attest_route", "shard_id": config.Shard, "routing_epoch": config.Epoch}); err != nil {
		return err
	}
	frame, err := ReadFrame(conn)
	if err != nil {
		return err
	}
	fields, err := exactObject(frame, "type", "shard_id", "routing_epoch")
	if err != nil {
		return err
	}
	var kind, shard string
	var epoch int64
	if json.Unmarshal(fields["type"], &kind) != nil || json.Unmarshal(fields["shard_id"], &shard) != nil || json.Unmarshal(fields["routing_epoch"], &epoch) != nil || kind != "route_attested" || shard != config.Shard || epoch != config.Epoch {
		return ErrProtocol
	}
	return nil
}
