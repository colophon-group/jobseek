package queue

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

// CurrentWorkdayDetail captures canonical data for one already claimed detail.
// Its private claim/profile binding cannot activate ownership or pop work.
type CurrentWorkdayDetail struct {
	claim             *Claim
	profile           WorkdayDetailProfile
	DescriptionHash   *int64
	Titles            []string
	LocationIDs       []int64
	EmploymentType    *string
	Schedulable       bool
	PublisherReserved bool
}

// PostingID returns the canonical posting bound to the opaque attempt.
func (d *CurrentWorkdayDetail) PostingID() string {
	if d == nil || d.claim == nil {
		return ""
	}
	return d.claim.task.ID
}

func (d *CurrentWorkdayDetail) Profile() WorkdayDetailProfile {
	if d == nil {
		return WorkdayDetailProfile{}
	}
	p := d.profile
	p.FacilityTenantAliases = append([]string(nil), p.FacilityTenantAliases...)
	if p.JSONLDConfig != nil {
		body, _ := json.Marshal(p.JSONLDConfig)
		json.Unmarshal(body, &p.JSONLDConfig)
	}
	return p
}

func (a *Authority) currentWorkdayDetail(ctx context.Context, tx pgx.Tx, claim *Claim) (*CurrentWorkdayDetail, error) {
	if !a.valid(claim) || claim.task.Kind != Scrape || (claim.task.Worker != Simple && claim.task.Worker != Browser) || (claim.task.Config["scrape_step"] != "" && claim.task.Config["scrape_step"] != "0") {
		return nil, ErrUnsupportedProfile
	}
	_, boardConfig, err := a.observeDetailOwnershipState(ctx, tx, claim.boardID, false)
	if err != nil {
		return nil, err
	}
	d := &CurrentWorkdayDetail{claim: claim}
	var source string
	var active, postingReserved, boardReserved bool
	var due *time.Time
	if err := tx.QueryRow(ctx, `SELECT p.source_url,p.description_r2_hash,p.is_active,p.next_scrape_at,
 p.tdm_reserved,b.tdm_reserved,p.titles,p.location_ids,p.employment_type
 FROM job_posting p JOIN job_board b ON b.id=p.board_id
 WHERE p.id=$1::uuid AND p.board_id=$2::uuid FOR UPDATE OF p`, claim.task.ID, claim.boardID).Scan(
		&source, &d.DescriptionHash, &active, &due, &postingReserved, &boardReserved, &d.Titles, &d.LocationIDs, &d.EmploymentType); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrAuthorityLost
		}
		return nil, err
	}
	if source != claim.task.Config["source_url"] || claim.task.Config["board_id"] != claim.boardID {
		return nil, ErrAuthorityLost
	}
	d.profile, err = inspectDetail(claim.boardID, boardConfig, source, claim.task.Worker)
	if err != nil {
		return nil, err
	}
	if claim.task.Domain != d.profile.Domain {
		return nil, ErrAuthorityLost
	}
	d.Schedulable = active && due != nil
	d.PublisherReserved = postingReserved || boardReserved
	return d, nil
}

// ReadWorkdayDetail revalidates canonical posting ownership and current board
// configuration under the existing opaque attempt, epoch and lease barriers.
// The native executor must inspect reservation/schedule state before fetching.
func (a *Authority) ReadWorkdayDetail(ctx context.Context, claim *Claim) (*CurrentWorkdayDetail, error) {
	if a == nil || !a.valid(claim) || claim.recovered != nil {
		return nil, ErrConfiguration
	}
	var detail *CurrentWorkdayDetail
	err := a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		if err := a.requireOwnership(ctx, tx, claim); err != nil {
			return err
		}
		if err := a.current(ctx, claim); err != nil {
			return err
		}
		if _, err := a.require(ctx, tx, claim, "active"); err != nil {
			return err
		}
		var err error
		detail, err = a.currentWorkdayDetail(ctx, tx, claim)
		return err
	})
	if err != nil {
		return nil, authorityError(err)
	}
	return detail, nil
}

// WriteWorkdayDetail encloses the existing native detail SQL writer and its
// canonical deadline receipt in the same attempt transaction. Preparation runs
// outside it. A changed posting/board route or opt-out cannot commit old data.
func (a *Authority) WriteWorkdayDetail(ctx context.Context, detail *CurrentWorkdayDetail, fn func(context.Context, pgx.Tx) error) (*Receipt, error) {
	if a == nil || detail == nil || !a.valid(detail.claim) || fn == nil {
		return nil, ErrConfiguration
	}
	receipt, err := a.Write(ctx, detail.claim, true, func(ctx context.Context, tx pgx.Tx) error {
		current, err := a.currentWorkdayDetail(ctx, tx, detail.claim)
		if err != nil {
			return err
		}
		if current.PublisherReserved {
			return ErrPublisherReserved
		}
		if !current.Schedulable || current.profile.EffectiveBoardSHA256 != detail.profile.EffectiveBoardSHA256 || current.profile.SourceURL != detail.profile.SourceURL {
			return ErrAuthorityLost
		}
		return fn(ctx, tx)
	})
	if err != nil {
		return nil, err
	}
	receipt.terminalOutcome = "succeeded"
	return receipt, nil
}

// FinishWorkdayDetail records a non-content outcome with fresh canonical policy
// and schedule observations. The same opaque attempt owns its final deadline;
// a reservation racing a fetch cannot consume a failure budget.
func (a *Authority) FinishWorkdayDetail(ctx context.Context, detail *CurrentWorkdayDetail, run *GreenhouseHostRun, observation GreenhouseHostObservation, fn func(context.Context, pgx.Tx, *CurrentWorkdayDetail, *HostCircuitOutcome) (string, error)) (*Receipt, error) {
	if a == nil || detail == nil || !a.valid(detail.claim) || fn == nil {
		return nil, ErrConfiguration
	}
	outcome := ""
	receipt, err := a.Write(ctx, detail.claim, true, func(ctx context.Context, tx pgx.Tx) error {
		current, err := a.currentWorkdayDetail(ctx, tx, detail.claim)
		if err != nil {
			return err
		}
		if current.profile.EffectiveBoardSHA256 != detail.profile.EffectiveBoardSHA256 || current.profile.SourceURL != detail.profile.SourceURL {
			return ErrAuthorityLost
		}
		var circuit *HostCircuitOutcome
		if run != nil && current.Schedulable && !current.PublisherReserved {
			if run.authority != a || run.claim != detail.claim || !run.preflightDone || run.deferUntil != nil {
				return ErrConfiguration
			}
			circuit, err = run.failureOutcome(ctx, observation)
			if err != nil {
				return err
			}
		}
		outcome, err = fn(ctx, tx, current, circuit)
		if err != nil {
			return err
		}
		switch outcome {
		case "failed", "publisher_reserved", "unscheduled", "host_circuit_open", "host_circuit_half_open":
		default:
			return ErrConfiguration
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	receipt.terminalOutcome = outcome
	return receipt, nil
}

// BeginWorkdayDetail prevents duplicate origin execution on one opaque attempt.
// Host preflight runs first; preparation and fetch retain the worker's heartbeat.
func (a *Authority) BeginWorkdayDetail(ctx context.Context, claim *Claim) (*CurrentWorkdayDetail, error) {
	detail, err := a.ReadWorkdayDetail(ctx, claim)
	if err != nil {
		return nil, err
	}
	claim.cycleMu.Lock()
	defer claim.cycleMu.Unlock()
	if claim.cycleStarted {
		return nil, ErrConfiguration
	}
	claim.cycleStarted = true
	return detail, nil
}
