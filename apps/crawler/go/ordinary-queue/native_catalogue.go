package queue

import (
	"context"
	"github.com/jackc/pgx/v5"
)

// OpenDefaultAuthority serves the whole catalogue after mixed-owner migration
// has been retired. It uses the existing explicit routing epoch and all normal
// SQL/token/lease fences. It cannot stage a plan, allocate an epoch, remove a
// guard, change routing or adopt an active mixed-owner deployment.
func OpenDefaultAuthority(ctx context.Context, dsn string, client *Client, epoch int64) (*Authority, error) {
	a, err := OpenAuthority(ctx, dsn, client, epoch)
	if err != nil {
		return nil, err
	}
	a.defaultOwner = true
	err = a.ownershipAdminTransaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		if err := a.requireOwnership(ctx, tx, nil); err != nil {
			return err
		}
		rows, err := tx.Query(ctx, "SELECT id::text FROM public.job_board WHERE is_enabled ORDER BY id")
		if err != nil {
			return err
		}
		ids := []string{}
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				rows.Close()
				return err
			}
			ids = append(ids, id)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
		pairs, err := a.observeOwnershipConfigs(ctx, tx, ids, false)
		if err != nil {
			return err
		}
		for _, id := range ids {
			pair := pairs[id]
			if _, _, err := inspectMonitorConfigs(id, pair.canonical, pair.cached); err != nil {
				return err
			}
			if _, _, err := inspectDetailConfigs(id, pair.canonical, pair.cached); err != nil {
				return err
			}
		}
		return a.verifyDefaultProjection(ctx)
	})
	if err != nil {
		a.Close()
		return nil, err
	}
	return a, nil
}

// This is a read-only guard. Loss, corruption or reappearance of either old
// ownership mechanism never causes reconstruction or fallback.
func (a *Authority) verifyDefaultProjection(ctx context.Context) error {
	const program = `
if redis.call('TYPE',KEYS[1])['ok'] ~= 'none' then return 0 end
local kind=redis.call('TYPE',KEYS[2])['ok']
if kind ~= 'none' and kind ~= 'hash' then return 0 end
if kind == 'hash' and redis.call('HLEN',KEYS[2]) > 0 then return 0 end
return 1`
	ok, err := a.queue.redis.EvalRO(ctx, program, []string{ownershipProjectionKey, "lightpanda-b0:legacy-guard"}).Int()
	if err != nil {
		return ErrObservation
	}
	if ok != 1 {
		return ErrAuthorityLost
	}
	return nil
}

// ValidateNativeBoard checks both configured execution paths using the same
// parsers as native claims. This pure check performs no I/O and grants no lease,
// ownership or permission to fetch a board. Runtime claims still bind the actual
// canonical board, posting, cache, routing epoch and attempt token.
func ValidateNativeBoard(boardID string, config map[string]string) error {
	if _, err := InspectRichMonitor(boardID, config); err != nil {
		return err
	}
	if _, err := inspectDetailOwnership(boardID, config); err != nil {
		return err
	}
	return nil
}
