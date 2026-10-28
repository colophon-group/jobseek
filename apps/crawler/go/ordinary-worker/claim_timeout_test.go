package worker

import (
	"context"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestNjoynBudgetPreservesOtherClaimsAndOperatorCaps(t *testing.T) {
	for _, explicit := range []string{"", "30", "600", "1800"} {
		env := fixtureRuntimeEnvironment()
		env["ORDINARY_GO_TASK_TIMEOUT_SECONDS"] = explicit
		c, err := ReadRuntimeConfig(func(k string) string { return env[k] }, strings.Repeat("a", 40))
		if err != nil {
			t.Fatal(err)
		}
		for _, task := range []queue.Task{
			{Kind: queue.Monitor, Worker: queue.Browser, Config: map[string]string{"crawler_type": "njoyn"}},
			{Kind: queue.Monitor, Worker: queue.Simple, Config: map[string]string{"crawler_type": "njoyn"}},
			{Kind: queue.Scrape, Worker: queue.Browser, Config: map[string]string{"crawler_type": "njoyn"}},
			{Kind: queue.Monitor, Worker: queue.Browser, Config: map[string]string{"crawler_type": "brassring"}},
			{},
		} {
			want := c.taskTimeout
			if explicit == "" && task.Kind == queue.Monitor && task.Worker == queue.Browser && task.Config["crawler_type"] == "njoyn" {
				want = 20 * time.Minute
			}
			if got := runtimeClaimTimeout(c, task); got != want {
				t.Fatal("claim budget changed outside provider/default scope")
			}
		}
	}
}

func TestNjoynBudgetCannotExtendParentCancellation(t *testing.T) {
	parent, cancel := context.WithCancel(context.Background())
	cancel()
	c := RuntimeConfig{taskTimeout: 10 * time.Minute, njoynTaskTimeout: 20 * time.Minute}
	task := queue.Task{Kind: queue.Monitor, Worker: queue.Browser, Config: map[string]string{"crawler_type": "njoyn"}}
	ctx, stop := context.WithTimeout(parent, runtimeClaimTimeout(c, task))
	defer stop()
	if ctx.Err() != context.Canceled {
		t.Fatal("provider budget escaped parent cancellation")
	}
}
