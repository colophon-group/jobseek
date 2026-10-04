package queue

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

// Redis due scores remain queue authority. A bounded candidate observation
// resolves each actual SQL posting before the original atomic Lua pop; mutable
// scrape metadata can only reject a candidate, never grant a foreign board.
func (a *Authority) detailCandidates(ctx context.Context, tx pgx.Tx, members []ownershipMember, start, count int, now float64) ([]ownershipCandidate, error) {
	if len(a.ownership.document.Details) == 0 {
		return nil, nil
	}
	binding := make(map[string]ownershipDetail, len(a.ownership.document.Details))
	for _, detail := range a.ownership.document.Details {
		binding[detail.BoardID] = detail
	}
	visited := map[string]bool{}
	var candidates []ownershipCandidate
	for i := 0; i < count; i++ {
		member := members[(start+i)%len(members)]
		detail, owned := binding[member.BoardID]
		if !owned || visited[detail.Domain] {
			continue
		}
		if len(visited) == 8 {
			break
		}
		visited[detail.Domain] = true
		for index, prefix := range []string{"ft_scrapes_simple:", "scrapes_simple:"} {
			rows, err := a.queue.redis.ZRangeByScoreWithScores(ctx, prefix+detail.Domain, &redis.ZRangeBy{Min: "-inf", Max: number(now), Count: ownershipCandidateBatch}).Result()
			if err != nil {
				return nil, ErrObservation
			}
			for _, row := range rows {
				id, ok := row.Member.(string)
				if !ok || !canonicalUUID.MatchString(id) {
					return nil, ErrProtocol
				}
				cached, err := a.queue.redis.HGetAll(ctx, "scrape:"+id).Result()
				if err != nil {
					return nil, ErrObservation
				}
				board := cached["board_id"]
				actualBinding, selected := binding[board]
				if !selected || actualBinding.Domain != detail.Domain || cached["domain"] != detail.Domain {
					continue
				}
				var selectedMember ownershipMember
				for _, m := range members {
					if m.BoardID == board {
						selectedMember = m
						break
					}
				}
				profile, boardConfig, err := a.observeGreenhouseMonitor(ctx, tx, board)
				if err != nil {
					return nil, err
				}
				if profile.EffectiveConfigSHA256 != selectedMember.EffectiveConfigHash {
					return nil, ErrAuthorityLost
				}
				var source string
				err = tx.QueryRow(ctx, "SELECT source_url FROM job_posting WHERE id=$1::uuid AND board_id=$2::uuid FOR UPDATE", id, board).Scan(&source)
				if errors.Is(err, pgx.ErrNoRows) {
					continue
				}
				if err != nil {
					return nil, err
				}
				if source != cached["source_url"] || (cached["scrape_step"] != "" && cached["scrape_step"] != "0") {
					continue
				}
				admitted, err := InspectWorkdayDetail(board, boardConfig, source, Simple)
				if err != nil || admitted.Domain != detail.Domain || admitted.Profile != actualBinding.Profile {
					continue
				}
				tier := 0
				if index == 1 {
					tier = 2
				}
				candidates = append(candidates, ownershipCandidate{member: selectedMember, tier: tier, score: row.Score, postingID: id, cached: cached})
				if len(candidates) == ownershipCandidateBatch {
					return candidates, nil
				}
			}
		}
	}
	return candidates, nil
}
