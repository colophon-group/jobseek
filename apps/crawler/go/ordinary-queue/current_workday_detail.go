package queue

import (
	"bytes"
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
	orderedStep       int
	orderedCount      int
	ownershipProfile  string
}

// OrderedStage reports the canonical, cached stage already bound to this claim.
func (d *CurrentWorkdayDetail) OrderedStage() (int, bool) {
	if d == nil {
		return 0, false
	}
	return d.orderedStep, d.orderedCount > 1
}

func (d *CurrentWorkdayDetail) HasNextOrderedStage() bool {
	return d != nil && d.orderedCount > 1 && d.orderedStep+1 < d.orderedCount
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
	p.EnrichmentFields = append([]string(nil), p.EnrichmentFields...)
	if p.HTTPAPIConfig != nil {
		body, _ := json.Marshal(p.HTTPAPIConfig)
		decoder := json.NewDecoder(bytes.NewReader(body))
		decoder.UseNumber()
		decoder.Decode(&p.HTTPAPIConfig)
	}
	if p.EmbeddedConfig != nil {
		body, _ := json.Marshal(p.EmbeddedConfig)
		json.Unmarshal(body, &p.EmbeddedConfig)
	}
	if p.OracleFields != nil {
		body, _ := json.Marshal(p.OracleFields)
		json.Unmarshal(body, &p.OracleFields)
	}
	if p.JSONLDRecovery != nil {
		value := *p.JSONLDRecovery
		p.JSONLDRecovery = &value
	}
	if p.JSONLDConfig != nil {
		body, _ := json.Marshal(p.JSONLDConfig)
		json.Unmarshal(body, &p.JSONLDConfig)
	}
	if p.DOMConfig != nil {
		body, _ := json.Marshal(p.DOMConfig)
		p.DOMConfig = nil
		decoder := json.NewDecoder(bytes.NewReader(body))
		decoder.UseNumber()
		decoder.Decode(&p.DOMConfig)
	}
	return p
}

func (a *Authority) currentWorkdayDetail(ctx context.Context, tx pgx.Tx, claim *Claim) (*CurrentWorkdayDetail, error) {
	if !a.valid(claim) || claim.task.Kind != Scrape || (claim.task.Worker != Simple && claim.task.Worker != Browser) {
		return nil, ErrUnsupportedProfile
	}
	boardProfile, boardConfig, err := a.observeDetailOwnershipState(ctx, tx, claim.boardID, false)
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
	d.Schedulable = active && due != nil
	d.PublisherReserved = postingReserved || boardReserved
	if a.defaultOwner && !d.Schedulable {
		// Retired postings may retain old URLs, lanes and fallback stages.
		// Their no-fetch settlement needs the verified canonical board binding,
		// not permission to fetch an obsolete URL. Posting identity and the
		// opaque lease were checked above; settlement rechecks eligibility.
		d.profile = boardProfile
		d.profile.SourceURL = source
		d.ownershipProfile = boardProfile.Profile
		return d, nil
	}
	d.profile, err = inspectDetail(claim.boardID, boardConfig, source, claim.task.Worker)
	if err != nil {
		return nil, err
	}
	d.ownershipProfile = d.profile.Profile
	if isOrderedDetailProfile(d.profile.Profile) {
		chain, e := InspectOrderedDetail(claim.boardID, boardConfig, source, claim.task.Worker)
		if e != nil {
			return nil, e
		}
		step, e := orderedDetailStep(claim.task.Config["scrape_step"], len(chain.Stages))
		if e != nil {
			return nil, e
		}
		d.orderedStep, d.orderedCount, d.profile = step, len(chain.Stages), chain.Stages[step]
	} else if claim.task.Config["scrape_step"] != "" && claim.task.Config["scrape_step"] != "0" {
		return nil, ErrUnsupportedProfile
	}
	if claim.task.Domain != d.profile.Domain {
		return nil, ErrAuthorityLost
	}
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
	var next *detailHandoff
	if detail.orderedCount > 1 && detail.orderedStep+1 < detail.orderedCount {
		next = &detailHandoff{step: int16(detail.orderedStep + 1)}
	}
	receipt, err := a.writeWithDetailHandoff(ctx, detail.claim, true, nil, next, func(ctx context.Context, tx pgx.Tx) error {
		current, err := a.currentWorkdayDetail(ctx, tx, detail.claim)
		if err != nil {
			return err
		}
		if current.PublisherReserved {
			return ErrPublisherReserved
		}
		if !current.Schedulable || current.orderedStep != detail.orderedStep || current.orderedCount != detail.orderedCount || current.profile.EffectiveBoardSHA256 != detail.profile.EffectiveBoardSHA256 || current.profile.SourceURL != detail.profile.SourceURL {
			return ErrAuthorityLost
		}
		if next != nil {
			next.hash = current.DescriptionHash
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
