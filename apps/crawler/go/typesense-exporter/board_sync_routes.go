package main

import (
	"context"
	"errors"
	"strings"

	"github.com/redis/go-redis/v9"
)

// Registry publication already enqueued each committed canonical schedule.
// Census all four monitor queue families so a former domain alias cannot hide
// outside the board cache's current domain. Never erase a live source attempt.
func retirePreviousMonitorRoutes(ctx context.Context, client *redis.Client, schedules []boardSyncSchedule, laneSHA string) error {
	targets := map[string]boardSyncSchedule{}
	for _, s := range schedules {
		if flag := s.Config["monitor_needs_browser"]; flag == "0" || flag == "1" {
			targets[s.BoardID] = s
		}
	}
	if len(targets) == 0 {
		return nil
	}
	type route struct{ worker, domain, id string }
	candidates := []route{}
	keys, members := map[string]bool{}, map[string]bool{}
	var cursor uint64
	for {
		batch, next, err := client.Scan(ctx, cursor, "*monitors_*:*", 1000).Result()
		if err != nil {
			return errors.New("monitor route census failed")
		}
		for _, key := range batch {
			if keys[key] {
				continue
			}
			keys[key] = true
			if len(keys) > 250000 {
				return errors.New("monitor route key census exceeds bound")
			}
			worker, domain := "", ""
			for _, w := range []string{"simple", "browser"} {
				for _, prefix := range []string{"ft_monitors_", "monitors_"} {
					if suffix, ok := strings.CutPrefix(key, prefix+w+":"); ok {
						worker, domain = w, suffix
					}
				}
			}
			if worker == "" {
				continue
			}
			if domain == "" || len(domain) > 253 || strings.ContainsAny(domain, "|\x00\r\n") {
				return errors.New("monitor previous domain is invalid")
			}
			var inner uint64
			for {
				values, next, err := client.ZScan(ctx, key, inner, "*", 1000).Result()
				if err != nil || len(values)%2 != 0 {
					return errors.New("monitor route membership census failed")
				}
				for i := 0; i < len(values); i += 2 {
					id := values[i]
					identity := key + "|" + id
					if members[identity] {
						continue
					}
					members[identity] = true
					if len(members) > 250000 {
						return errors.New("monitor route membership census exceeds bound")
					}
					s, exists := targets[id]
					if !exists {
						continue
					}
					selected := "simple"
					if s.Browser {
						selected = "browser"
					}
					if selected != worker || s.Domain != domain {
						candidates = append(candidates, route{worker, domain, id})
					}
				}
				inner = next
				if inner == 0 {
					break
				}
			}
		}
		cursor = next
		if cursor == 0 {
			break
		}
	}
	// Finish the bounded read census before any alias retirement. Every Lua
	// invocation rechecks cache, ownership, live tokens and canonical presence.
	for start := 0; start < len(candidates); start += 256 {
		pipe := client.Pipeline()
		for _, old := range candidates[start:min(start+256, len(candidates))] {
			s := targets[old.id]
			selected := "simple"
			if s.Browser {
				selected = "browser"
			}
			pipe.EvalSha(ctx, laneSHA, nil, selected, s.Domain, old.id, old.worker, old.domain)
		}
		if _, err := pipe.Exec(ctx); err != nil {
			return errors.New("previous monitor route retirement was not fully acknowledged")
		}
	}
	return nil
}
