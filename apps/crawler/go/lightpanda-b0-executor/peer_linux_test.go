//go:build linux

package executor

import (
	"context"
	"net"
	"testing"
	"time"
)

func TestLinuxSocketUsesRealSameUIDCredentials(t *testing.T) {
	path, cancel, done := startTestServer(t, &testAttestor{}, func(context.Context, *net.UnixConn, Request) error { return nil }, executorPeerUID)
	conn := unixConversation(t, path)
	if err := WriteMessage(conn, map[string]any{"version": Protocol, "type": "attest_route", "shard_id": "lightpanda-b0", "routing_epoch": 7}); err != nil {
		t.Fatal(err)
	}
	if readKind(t, conn) != "route_attested" {
		t.Fatal("real same-UID peer rejected")
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("shutdown failed")
	}
}
