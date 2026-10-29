package main

import (
	"fmt"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Validate the actual projection produced by the still-uncommitted registry
// transaction. It includes canonical IDs and metadata retained by the original
// SQL merge, including discovered sitemap roots. Raw CSV rows cannot substitute
// for that identity. Failure rolls back before any Redis publication.
func validateNativeRegistrySchedules(schedules []boardSyncSchedule) error {
	for i, schedule := range schedules {
		config := make(map[string]string, len(schedule.Config)+1)
		for k, v := range schedule.Config {
			config[k] = v
		}
		config["domain"] = schedule.Domain
		if err := queue.ValidateNativeBoard(schedule.BoardID, config); err != nil {
			return fmt.Errorf("native registry eligibility rejected row %d", i+1)
		}
	}
	return nil
}
