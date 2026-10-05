package queue

import (
	"context"
	"errors"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

// Qualify the UUID column: the selected id::text otherwise shadows it in
// ORDER BY and forces PostgreSQL to sort the entire board before LIMIT.
const detailPostingCursorQuery = "SELECT id::text,source_url FROM job_posting WHERE board_id=$1::uuid AND id>$2::uuid ORDER BY job_posting.id LIMIT 64"

// Due Redis representations remain the pop authority. Each bounded observation
// resolves the actual SQL posting and full canonical board before original Lua
// checks priority, fairness, throttle, configuration and exclusive ownership.
func (a *Authority) detailCandidates(ctx context.Context, tx pgx.Tx, now float64, worker WorkerType) ([]ownershipCandidate, error) {
	details := make([]ownershipDetail, 0, len(a.ownership.document.Details))
	for _, detail := range a.ownership.document.Details {
		if detail.Worker == worker {
			details = append(details, detail)
		}
	}
	if len(details) == 0 {
		return nil, nil
	}
	a.ownershipMu.Lock()
	if a.detailCursors == nil {
		a.detailCursors = map[WorkerType]int{}
	}
	start := a.detailCursors[worker] % len(details)
	count := min(8, len(details))
	a.detailCursors[worker] = (start + 1) % len(details)
	a.ownershipMu.Unlock()
	bindings := make(map[string]ownershipDetail, len(details))
	for _, detail := range details {
		bindings[detail.BoardID] = detail
	}
	var candidates []ownershipCandidate
	type boardObservation struct {
		profile WorkdayDetailProfile
		config  map[string]string
	}
	observations := map[string]boardObservation{}
	visited := map[string]bool{}
	add := func(id, board, source, domain string, score float64, tier int, cached map[string]string) error {
		binding, selected := bindings[board]
		if !selected {
			return nil
		}
		if cached["board_id"] != board || cached["source_url"] != source || cached["domain"] != domain || (cached["scrape_step"] != "" && cached["scrape_step"] != "0") {
			return nil
		}
		observed, ok := observations[board]
		if !ok {
			profile, config, err := a.observeDetailOwnershipState(ctx, tx, board, false)
			if err != nil {
				return err
			}
			observed = boardObservation{profile: profile, config: config}
			observations[board] = observed
		}
		profile, boardConfig := observed.profile, observed.config
		member, err := a.ownership.detailContext(binding)
		if err != nil {
			return err
		}
		if profile.EffectiveBoardSHA256 != member.EffectiveConfigHash {
			return ErrAuthorityLost
		}
		var actual string
		err = tx.QueryRow(ctx, "SELECT source_url FROM job_posting WHERE id=$1::uuid AND board_id=$2::uuid FOR UPDATE", id, board).Scan(&actual)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if source != actual {
			return nil
		}
		admitted, err := inspectDetail(board, boardConfig, actual, worker)
		if err != nil || admitted.Profile != binding.Profile || !detailDomainMatches(binding.Domain, admitted) || admitted.Domain != domain {
			return nil
		}
		candidates = append(candidates, ownershipCandidate{member: member, tier: tier, score: score, postingID: id, cached: cached})
		return nil
	}
	for i := 0; i < count; i++ {
		detail := details[(start+i)%len(details)]
		if detail.Domain == "*" {
			// Keyset traversal of canonical posting IDs supports boards whose URLs
			// span hosts without a second domain/ownership registry. Redis alone
			// decides whether an observed posting is actually due. Future SQL receipts
			// are included so interrupted commit-before-ACK attempts can recover.
			a.ownershipMu.Lock()
			cursor := a.detailPostingCursor[detail.BoardID]
			a.ownershipMu.Unlock()
			if cursor == "" {
				cursor = "00000000-0000-0000-0000-000000000000"
			}
			rows, err := tx.Query(ctx, detailPostingCursorQuery, detail.BoardID, cursor)
			if err != nil {
				return nil, err
			}
			type posting struct{ id, source string }
			var postings []posting
			for rows.Next() {
				var p posting
				if err := rows.Scan(&p.id, &p.source); err != nil {
					rows.Close()
					return nil, err
				}
				postings = append(postings, p)
			}
			err = rows.Err()
			rows.Close()
			if err != nil {
				return nil, err
			}
			next := ""
			if len(postings) == 64 {
				next = postings[len(postings)-1].id
			}
			a.ownershipMu.Lock()
			if a.detailPostingCursor == nil {
				a.detailPostingCursor = map[string]string{}
			}
			a.detailPostingCursor[detail.BoardID] = next
			a.ownershipMu.Unlock()
			// One bounded pipeline per board avoids one network round trip per
			// posting. Lua checks the current snapshot, priority and score before a pop.
			type queuedPosting struct {
				posting
				domain string
				config *redis.MapStringStringCmd
				scores [2]*redis.FloatCmd
			}
			pipe := a.queue.redis.Pipeline()
			queued := make([]queuedPosting, 0, len(postings))
			for _, p := range postings {
				domain, err := detailSourceDomain(detail, p.source)
				if err != nil {
					continue
				}
				q := queuedPosting{posting: p, domain: domain, config: pipe.HGetAll(ctx, "scrape:"+p.id)}
				for index, prefix := range []string{"ft_scrapes_" + string(worker) + ":", "scrapes_" + string(worker) + ":"} {
					q.scores[index] = pipe.ZScore(ctx, prefix+domain, p.id)
				}
				queued = append(queued, q)
			}
			if _, err := pipe.Exec(ctx); err != nil && !errors.Is(err, redis.Nil) {
				return nil, ErrObservation
			}
			for _, p := range queued {
				cached, err := p.config.Result()
				if err != nil {
					return nil, ErrObservation
				}
				for index, command := range p.scores {
					score, err := command.Result()
					if errors.Is(err, redis.Nil) {
						continue
					}
					if err != nil {
						return nil, ErrObservation
					}
					if score > now {
						continue
					}
					tier := 0
					if index == 1 {
						tier = 2
					}
					if err := add(p.id, detail.BoardID, p.source, p.domain, score, tier, cached); err != nil {
						return nil, err
					}
					break
				}
				if len(candidates) == ownershipCandidateBatch {
					return candidates, nil
				}
			}
			continue
		}
		if visited[detail.Domain] {
			continue
		}
		visited[detail.Domain] = true
		for index, prefix := range []string{"ft_scrapes_" + string(worker) + ":", "scrapes_" + string(worker) + ":"} {
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
				binding, owned := bindings[board]
				if !owned || binding.Domain != detail.Domain || cached["domain"] != detail.Domain {
					continue
				}
				source := cached["source_url"]
				if strings.ContainsRune(source, 0) {
					continue
				}
				tier := 0
				if index == 1 {
					tier = 2
				}
				if err := add(id, board, source, detail.Domain, row.Score, tier, cached); err != nil {
					return nil, err
				}
				if len(candidates) == ownershipCandidateBatch {
					return candidates, nil
				}
			}
		}
	}
	return candidates, nil
}
