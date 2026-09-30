package executor

import (
	"context"
	"net"
	"testing"
)

func TestNativeHealthUsesReservedRouteAndRejectsStaleEpoch(t *testing.T) {
	store := &testAttestor{}
	path, _, _ := startTestServer(t, store, func(context.Context, *net.UnixConn, Request) error {
		t.Error("health reached task handler")
		return ErrProtocol
	}, sameTestPeer)
	config := RuntimeConfig{Shard: "lightpanda-b0", Epoch: 7}
	if err := checkHealth(context.Background(), config, path); err != nil {
		t.Fatal(err)
	}
	if store.calls.Load() != 2 {
		t.Fatal("health did not attest database epoch")
	}
	store.stale.Store(true)
	if err := checkHealth(context.Background(), config, path); err == nil {
		t.Fatal("stale epoch reported healthy")
	}
	store.stale.Store(false)
	config.Epoch = 8
	if err := checkHealth(context.Background(), config, path); err == nil {
		t.Fatal("changed route reported healthy")
	}
}
