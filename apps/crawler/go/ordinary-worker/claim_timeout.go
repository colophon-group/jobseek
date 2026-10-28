package worker

import (
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func runtimeClaimTimeout(c RuntimeConfig, task queue.Task) time.Duration {
	if c.njoynTaskTimeout > 0 && task.Kind == queue.Monitor && task.Worker == queue.Browser && task.Config["crawler_type"] == "njoyn" {
		return c.njoynTaskTimeout
	}
	return c.taskTimeout
}
