package queue

import (
	"context"
	_ "embed"
	"net/url"
	"strconv"

	"github.com/redis/go-redis/v9"
)

//go:embed enqueue_task.lua
var enqueueLua string

var enqueueScript = redis.NewScript(enqueueLua)

// EnqueueURLDetail uses the existing atomic Python queue/config ABI, including
// its B0 owner exclusion and first-time promotion rules. It does not authorize
// a database write or admit a native detail profile. The owned monitor calls it
// after committing canonical URL stubs; ambiguous enqueue errors are recovered
// by the existing missing-content inventory repair/reconciliation path.
func (c *Client) EnqueueURLDetail(ctx context.Context, detail URLOnlyDetail) (bool, error) {
	if c == nil || c.redis == nil || !canonicalUUID.MatchString(detail.ID) || !canonicalUUID.MatchString(detail.BoardID) || detail.Due.IsZero() {
		return false, ErrConfiguration
	}
	u, err := url.Parse(detail.URL)
	if err != nil || u.User != nil || (u.Scheme != "http" && u.Scheme != "https") || !validPart(u.Hostname()) || len(detail.URL) > 8192 {
		return false, ErrConfiguration
	}
	worker := Simple
	if detail.Browser {
		worker = Browser
	}
	first, score, hash := "0", seconds(detail.Due), ""
	if detail.DescriptionHash == nil {
		first = "1"
		score = 0
	} else {
		hash = strconv.FormatInt(*detail.DescriptionHash, 10)
	}
	if !validTime(score) {
		return false, ErrConfiguration
	}
	now, err := c.clock(ctx)
	if err != nil {
		return false, err
	}
	value, err := enqueueScript.Run(ctx, c.redis, nil, string(worker), u.Hostname(), detail.ID, number(score), string(Scrape), first, number(now), "source_url", detail.URL, "board_id", detail.BoardID, "description_r2_hash", hash, "scrape_step", "0").Int64()
	if err != nil {
		return false, ErrObservation
	}
	if value != 0 && value != 1 {
		return false, ErrProtocol
	}
	return value == 1, nil
}
