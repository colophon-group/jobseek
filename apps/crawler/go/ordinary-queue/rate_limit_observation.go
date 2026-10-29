package queue

import (
	"math"
	"strconv"
	"strings"

	"github.com/redis/go-redis/v9"
)

// An array keeps an absent key distinct without a top-level Redis nil reply
// masking later results in the existing candidate-observation pipeline.
const domainRateLimitObservation = `local value = redis.call('GET', KEYS[1]); if not value then return {} end; return {value}`

// This observation only avoids work that cannot yet be claimed. The atomic
// Lua pop still checks the current shared delay, ownership, route and lease.
func futureDomainRateLimit(command *redis.Cmd, now float64) (bool, error) {
	values, err := command.Slice()
	if err != nil {
		return false, ErrObservation
	}
	if len(values) == 0 {
		return false, nil
	}
	if len(values) != 1 {
		return false, ErrObservation
	}
	value, ok := values[0].(string)
	if !ok {
		return false, ErrObservation
	}
	deadline, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
	if err != nil || math.IsNaN(deadline) || math.IsInf(deadline, 0) {
		return false, ErrObservation
	}
	return deadline > now, nil
}
