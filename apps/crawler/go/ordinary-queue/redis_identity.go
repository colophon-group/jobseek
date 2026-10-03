package queue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"
)

// RedisInstanceSHA256 observes the server incarnation through read-only INFO.
// It proves neither socket ownership nor exclusion; those belong to the host
// coordinator. Values/errors never expose endpoints, credentials or INFO text.
func (c *Client) RedisInstanceSHA256(ctx context.Context) (string, error) {
	if ctx == nil || ctx.Err() != nil || c == nil || c.redis == nil {
		return "", ErrConfiguration
	}
	body, err := c.redis.Info(ctx, "server").Result()
	if err != nil || len(body) > 32768 {
		return "", ErrObservation
	}
	id, mode := "", ""
	seen := map[string]bool{}
	for _, line := range strings.Split(body, "\r\n") {
		if line == "" || strings.HasPrefix(line, "# ") {
			continue
		}
		key, value, ok := strings.Cut(line, ":")
		if !ok || seen[key] {
			return "", ErrObservation
		}
		seen[key] = true
		if key == "run_id" {
			id = value
		}
		if key == "redis_mode" {
			mode = value
		}
	}
	if mode != "standalone" || len(id) != 40 {
		return "", ErrObservation
	}
	for _, c := range id {
		if c < '0' || c > '9' {
			if c < 'a' || c > 'f' {
				return "", ErrObservation
			}
		}
	}
	h := sha256.Sum256([]byte(id))
	return hex.EncodeToString(h[:]), nil
}
