package queue

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"sort"
	"strconv"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

const ownershipProjectionKey = "ordinary:ownership:active"
const ownershipCandidateBatch = 64

// OpenOwnedAuthority binds one native owner to an externally verified exact
// plan/source/epoch. It cannot stage, activate or reconstruct Redis ownership.
func OpenOwnedAuthority(ctx context.Context, dsn string, client *Client, epoch int64, digest, revision string) (*Authority, error) {
	a, err := OpenAuthority(ctx, dsn, client, epoch)
	if err != nil {
		return nil, err
	}
	a.ownership, err = a.LoadActiveOwnership(ctx, digest, revision)
	if err == nil {
		err = client.verifyOwnershipProjection(ctx, a.ownership)
	}
	if err != nil {
		a.Close()
		return nil, err
	}
	return a, nil
}

func (c *Client) verifyOwnershipProjection(ctx context.Context, plan *OwnershipPlan) error {
	if plan == nil {
		return ErrConfiguration
	}
	body, err := c.redis.Get(ctx, ownershipProjectionKey).Result()
	if errors.Is(err, redis.Nil) {
		return ErrAuthorityLost
	}
	if err != nil {
		return ErrObservation
	}
	if body != plan.projection {
		return ErrAuthorityLost
	}
	return nil
}

func (plan *OwnershipPlan) claimBinding(role, id, snapshot string) []any {
	return []any{role, plan.digest, plan.ProjectionSHA1(), number(float64(plan.document.Epoch)), plan.document.SourceRevision, id, snapshot}
}

// ClaimLegacyBound is the queue ABI for an already attested legacy owner.
// A production caller must hold the DB lease/epoch barriers and freshly attest
// this exact active plan through the pop. This queue method is not that grant.
func (c *Client) ClaimLegacyBound(ctx context.Context, worker WorkerType, plan *OwnershipPlan) (*Task, error) {
	if plan == nil {
		return nil, ErrConfiguration
	}
	return c.claimTaskBound(ctx, worker, "", plan.claimBinding("legacy", "", ""))
}

func (a *Authority) requireOwnership(ctx context.Context, tx pgx.Tx, claim *Claim) error {
	if a.ownership == nil {
		return requireUnselectedAuthority(ctx, tx)
	}
	plan := a.ownership
	// Startup already validates every member and the exact payload hash. The
	// ownership transition trigger makes that payload immutable and retains
	// active/retired rows. Under the shared lease/epoch barriers, recheck the
	// exact active identity without transferring and decoding the whole fleet
	// for every heartbeat, claim, posting chunk and settlement.
	var active bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan
  WHERE plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND state='active')`,
		plan.digest, plan.SourceRevision(), a.epoch).Scan(&active); err != nil {
		return err
	}
	if !active {
		return ErrAuthorityLost
	}
	if err := a.queue.verifyOwnershipProjection(ctx, plan); err != nil {
		return err
	}
	if claim == nil {
		return nil
	}
	if (claim.task.Kind != Monitor && claim.task.Kind != Scrape) || claim.task.Worker != Simple {
		return ErrAuthorityLost
	}
	if claim.task.Kind == Scrape {
		for _, bound := range plan.document.Details {
			if bound.BoardID != claim.boardID {
				continue
			}
			context, err := plan.detailContext(bound)
			if err != nil {
				return err
			}
			detail, err := a.currentWorkdayDetail(ctx, tx, claim)
			if err != nil {
				return err
			}
			if !detailDomainMatches(bound.Domain, detail.profile) || detail.profile.EffectiveBoardSHA256 != context.EffectiveConfigHash || detail.profile.Profile != bound.Profile {
				return ErrAuthorityLost
			}
			return nil
		}
		return ErrAuthorityLost
	}
	var member *ownershipMember
	for i := range plan.document.Members {
		if plan.document.Members[i].BoardID == claim.boardID {
			member = &plan.document.Members[i]
			break
		}
	}
	if member == nil {
		return ErrAuthorityLost
	}
	if claim.task.Domain != member.Domain {
		return ErrAuthorityLost
	}
	profile, _, err := a.observeGreenhouseMonitor(ctx, tx, member.BoardID)
	if err != nil {
		return err
	}
	if profile.EffectiveConfigSHA256 != member.EffectiveConfigHash {
		return ErrAuthorityLost
	}
	return nil
}

type ownershipCandidate struct {
	member    ownershipMember
	tier      int
	score     float64
	postingID string
	cached    map[string]string
}

// Inspect a fixed-size cohort batch rather than repeatedly visiting a foreign
// global/domain head. The Lua pop still checks global priority, ready route,
// throttle, inflight identity and the complete canonical-validated snapshot.
func (a *Authority) claimOwned(ctx context.Context, tx pgx.Tx, worker WorkerType) (*Task, error) {
	if worker != Simple {
		return nil, ErrConfiguration
	}
	now, err := a.queue.clock(ctx)
	if err != nil {
		return nil, err
	}
	members := a.ownership.document.Members
	count := min(len(members), ownershipCandidateBatch)
	// Protect only the shared scan cursor. A canonical row wait must not hold
	// a process mutex across every other claim's SQL transaction. Redis still
	// atomically validates and pops the exact member/lease/snapshot below.
	a.ownershipMu.Lock()
	start := a.ownershipCursor
	a.ownershipCursor = (start + count) % len(members)
	a.ownershipMu.Unlock()
	type probe struct {
		member           ownershipMember
		first, recurring *redis.Cmd
	}
	probes := make([]probe, 0, count)
	pipe := a.queue.redis.Pipeline()
	for i := 0; i < count; i++ {
		member := members[(start+i)%len(members)]
		probes = append(probes, probe{member,
			pipe.Do(ctx, "ZMSCORE", "ft_monitors_simple:"+member.Domain, member.BoardID),
			pipe.Do(ctx, "ZMSCORE", "monitors_simple:"+member.Domain, member.BoardID)})
	}
	if _, err := pipe.Exec(ctx); err != nil && !errors.Is(err, redis.Nil) {
		return nil, ErrObservation
	}
	var candidates []ownershipCandidate
	for _, probe := range probes {
		for tier, command := range []*redis.Cmd{probe.first, probe.recurring} {
			score, err := ownershipProbeScore(command)
			if err != nil {
				return nil, err
			}
			if score != nil && *score <= now {
				candidates = append(candidates, ownershipCandidate{member: probe.member, tier: tier, score: *score})
				break
			}
		}
	}
	details, err := a.detailCandidates(ctx, tx, now)
	if err != nil {
		return nil, err
	}
	candidates = append(candidates, details...)
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].tier != candidates[j].tier {
			return candidates[i].tier < candidates[j].tier
		}
		if candidates[i].score != candidates[j].score {
			return candidates[i].score < candidates[j].score
		}
		return candidates[i].member.BoardID < candidates[j].member.BoardID
	})
	var rejected error
	blockedDomains := make(map[string]bool)
	for _, candidate := range candidates {
		domain := candidate.member.Domain
		if candidate.postingID != "" {
			domain = candidate.cached["domain"]
		}
		if blockedDomains[domain] {
			continue
		}
		role, cached := "native", candidate.cached
		if candidate.postingID != "" {
			role = "native_detail"
		} else {
			profile, observed, err := a.observeGreenhouseMonitor(ctx, tx, candidate.member.BoardID)
			if err != nil {
				rejected = err
				continue
			}
			if profile.EffectiveConfigSHA256 != candidate.member.EffectiveConfigHash {
				rejected = ErrAuthorityLost
				continue
			}
			cached = observed
		}
		body, err := json.Marshal(cached)
		if err != nil {
			return nil, ErrConfiguration
		}
		var token [16]byte
		if _, err := rand.Read(token[:]); err != nil {
			return nil, ErrObservation
		}
		binding := a.ownership.claimBinding(role, candidate.member.BoardID, string(body))
		if candidate.postingID != "" {
			binding = append(binding, candidate.postingID)
		}
		task, err := a.queue.claimTaskBound(ctx, worker, hex.EncodeToString(token[:]), binding)
		if err != nil || task != nil {
			return task, err
		}
		// A throttle or unavailable ready route for the oldest provider must
		// not hide another provider in this bounded batch. Lua retains all
		// global first-time/fairness checks for every attempted claim.
		blockedDomains[domain] = true
	}
	return nil, rejected
}

// ZMSCORE's array retains a nullable item without a top-level Redis nil reply.
// go-redis pipeline errors can otherwise mask a later populated ZSCORE result
// after an earlier missing representation. A real score of zero stays distinct.
func ownershipProbeScore(command *redis.Cmd) (*float64, error) {
	values, err := command.Slice()
	if err != nil {
		return nil, ErrObservation
	}
	if len(values) != 1 {
		return nil, ErrProtocol
	}
	if values[0] == nil {
		return nil, nil
	}
	raw, ok := values[0].(string)
	if !ok {
		return nil, ErrProtocol
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return nil, ErrProtocol
	}
	return &value, nil
}

// AttestOwnershipProjection compares the separately installed legacy projection
// identity with the exact native plan and fresh canonical/Redis active state.
// It grants no activation permission and never adopts a changed identity.
func (a *Authority) AttestOwnershipProjection(ctx context.Context, projection string) error {
	if a == nil || a.ownership == nil || a.ownership.ProjectionSHA1() != projection {
		return ErrAuthorityLost
	}
	return a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error { return a.requireOwnership(ctx, tx, nil) })
}
