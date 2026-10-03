package worker

import (
	"context"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

func TestRealWorkerHealthExecutableWithoutWorkerCredentials(t *testing.T) {
	binary := os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY")
	if binary == "" {
		binary = filepath.Join(t.TempDir(), "worker-health")
		build := exec.Command("go", "build", "-buildvcs=false", "-ldflags=-X main.sourceRevision="+ordinaryFixtureSourceRevision(t), "-o", binary, "./cmd/live")
		if out, err := build.CombinedOutput(); err != nil {
			t.Fatalf("health executable build failed: %v %s", err, out)
		}
	}
	var listener net.Listener
	var port string
	for _, candidate := range []string{"9095", "9096", "9097", "9098"} {
		var err error
		listener, err = net.Listen("tcp", "127.0.0.1:"+candidate)
		if err == nil {
			port = candidate
			break
		}
	}
	if port == "" {
		t.Fatal("no private worker-health fixture port available")
	}
	var status atomic.Int32
	status.Store(200)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.URL.Path != "/" {
			t.Error("health executable used another resource")
		}
		w.WriteHeader(int(status.Load()))
	})}
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(func() { _ = server.Close() })
	run := func(arg string) error {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, binary, arg)
		cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "HTTP_PROXY=http://127.0.0.1:1", "HTTPS_PROXY=http://127.0.0.1:1"}
		return cmd.Run()
	}
	if err := run("--worker-health=" + port); err != nil {
		t.Fatal("healthy root probe needed ownership/assets/credentials orusedproxy", err)
	}
	status.Store(503)
	if run("--worker-health="+port) == nil {
		t.Fatal("unhealthy worker passed installed probe")
	}
	if run("--worker-health=9093") == nil {
		t.Fatal("unconfigured port passed installed probe")
	}
	_ = server.Close()
	if run("--worker-health="+port) == nil {
		t.Fatal("stopped worker passed installed probe")
	}
}
