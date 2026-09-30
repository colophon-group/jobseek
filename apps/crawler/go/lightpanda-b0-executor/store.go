package executor

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"encoding/json"
	publisherpolicy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

const maxIdentityInteger = int64(9_999_999_999_999)

var (
	ErrAuthorityLost = errors.New("postgres_write_fence_rejected")
	canonicalUUID    = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	safeID           = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
	hexDigest        = regexp.MustCompile(`^[0-9a-f]{64}$`)
	claimToken       = regexp.MustCompile(`^([1-9][0-9]{0,12}):([1-9][0-9]{0,12})$`)
)

// Fence is derived from a validated Go-owned task and authorized Redis claim.
// This package has no Redis or renderer credential consumer.
type Fence struct {
	PostingID      string
	ShardID        string
	RoutingEpoch   int64
	ConfigRevision int64
	PayloadSHA256  string
	ClaimToken     string
}

func (f Fence) Validate() error {
	match := claimToken.FindStringSubmatch(f.ClaimToken)
	if !canonicalUUID.MatchString(f.PostingID) || !safeID.MatchString(f.ShardID) ||
		f.RoutingEpoch < 1 || f.RoutingEpoch > maxIdentityInteger ||
		f.ConfigRevision < 1 || f.ConfigRevision > maxIdentityInteger ||
		!hexDigest.MatchString(f.PayloadSHA256) || match == nil {
		return errors.New("invalid Lightpanda B0 write fence")
	}
	epoch, err := strconv.ParseInt(match[1], 10, 64)
	if err != nil || epoch != f.RoutingEpoch {
		return errors.New("claim epoch differs from write fence")
	}
	return nil
}

func (f Fence) args() []any {
	return []any{f.PostingID, f.ShardID, f.RoutingEpoch, "go", f.ConfigRevision, f.PayloadSHA256, f.ClaimToken}
}

type fenceRejection struct{ reason string }

func (e *fenceRejection) Error() string        { return "Lightpanda B0 write fence rejected: " + e.reason }
func (e *fenceRejection) Is(target error) bool { return target == ErrAuthorityLost }

func fenceError(err error) error {
	var failure *pgconn.PgError
	if errors.As(err, &failure) && failure.Code == "P0001" && failure.Message == "lightpanda_b0_write_fence_rejected" {
		// Only database-owned symbolic reasons cross the wire/log boundary.
		reason := "rejected"
		switch failure.Detail {
		case "config_revision_mismatch", "config_revision_not_monotonic", "engine_owner_mismatch",
			"generation_in_use", "generation_not_advanced", "invalid_claim_token", "invalid_identity",
			"job_posting_id_mismatch", "missing", "payload_change_requires_config_revision", "payload_digest_mismatch",
			"revoked", "revoked_replay", "route_change_requires_epoch", "routing_epoch_mismatch",
			"routing_epoch_not_current", "shard_id_mismatch", "claim_token_mismatch":
			reason = failure.Detail
		}
		return &fenceRejection{reason: reason}
	}
	return err
}

type Store struct{ pool *pgxpool.Pool }

// OpenStore preserves the B0 executor's one-connection budget. Callers must
// attest the current route before binding its private socket.
func OpenStore(ctx context.Context, dsn string) (*Store, error) {
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, errors.New("invalid executor database configuration")
	}
	config.MinConns, config.MaxConns = 1, 1
	config.MaxConnIdleTime = time.Minute
	config.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeDescribeExec
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:lightpanda-b0-executor:local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "30s"
	config.ConnConfig.RuntimeParams["idle_in_transaction_session_timeout"] = "60s"
	config.ConnConfig.RuntimeParams["tcp_keepalives_idle"] = "60"
	config.ConnConfig.RuntimeParams["tcp_keepalives_interval"] = "10"
	config.ConnConfig.RuntimeParams["tcp_keepalives_count"] = "3"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, errors.New("executor database pool unavailable")
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return &Store{pool: pool}, nil
}

func (s *Store) Close() { s.pool.Close() }

func (s *Store) AttestEpoch(ctx context.Context, expected int64) error {
	if expected < 1 || expected > maxIdentityInteger {
		return errors.New("invalid route epoch")
	}
	var last int64
	var called bool
	if err := s.pool.QueryRow(ctx, "SELECT last_value, is_called FROM public.lightpanda_b0_routing_epoch_seq").Scan(&last, &called); err != nil {
		return err
	}
	if !called || last != expected {
		return errors.New("executor routing epoch is not current")
	}
	return nil
}

func fenceCommand(ctx context.Context, tx pgx.Tx, operation string, f Fence) error {
	var query string
	switch operation {
	case "activate":
		query = "SELECT public.jobseek_lightpanda_b0_activate_write_fence($1, $2, $3, $4, $5, $6, $7)"
	case "require":
		query = "SELECT public.jobseek_lightpanda_b0_require_write_fence($1, $2, $3, $4, $5, $6, $7)"
	case "revoke":
		query = "SELECT public.jobseek_lightpanda_b0_revoke_write_fence($1, $2, $3, $4, $5, $6, $7)"
	default:
		return errors.New("unknown fence operation")
	}
	_, err := tx.Exec(ctx, query, f.args()...)
	return fenceError(err)
}

// Activate is a separate short transaction, as in the existing executor.
func (s *Store) Activate(ctx context.Context, f Fence) error {
	if err := f.Validate(); err != nil {
		return err
	}
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error { return fenceCommand(ctx, tx, "activate", f) })
}

// AuthoritativeWrite requires and revokes the exact claim in the transaction
// containing every posting/description/schedule effect. The conversation
// caller must hold its supervisor authorization until commit acknowledgement.
func (s *Store) AuthoritativeWrite(ctx context.Context, f Fence, write func(pgx.Tx) error) error {
	if err := f.Validate(); err != nil {
		return err
	}
	if write == nil {
		return errors.New("missing authoritative write")
	}
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if err := fenceCommand(ctx, tx, "require", f); err != nil {
			return err
		}
		if err := write(tx); err != nil {
			return err
		}
		return fenceCommand(ctx, tx, "revoke", f)
	})
}

type DescriptionCandidate struct {
	HTML, Locale string
	Hash         int64
}

// StageDescription hashes exact uploaded HTML bytes. The current scalar hash
// cannot skip a locale-row UPSERT; PostgreSQL performs byte deduplication.
func StageDescription(html, language string) (*DescriptionCandidate, error) {
	if html == "" {
		return nil, nil
	}
	if !utf8.ValidString(html) || !utf8.ValidString(language) || strings.ContainsRune(html, 0) || strings.ContainsRune(language, 0) {
		return nil, errors.New("description is not PostgreSQL UTF-8 text")
	}
	if language == "" {
		language = "en"
	}
	digest := sha256.Sum256([]byte(html))
	return &DescriptionCandidate{HTML: html, Locale: language, Hash: int64(binary.BigEndian.Uint64(digest[:8]))}, nil
}

type ContentFields struct {
	EmploymentType               *string
	Titles, Locales              []string
	LocationIDs                  []int64
	LocationTypes                []string
	TechnologyIDs                []int64
	SalaryMin, SalaryMax         *int64
	SalaryCurrency, SalaryPeriod *string
	SalaryEUR                    *int64
	ExperienceMin, ExperienceMax *float64
	OccupationID, SeniorityID    *int64
}

func (f ContentFields) args(postingID string) []any {
	return []any{postingID, f.EmploymentType, f.Titles, f.Locales, f.LocationIDs, f.LocationTypes, f.TechnologyIDs, f.SalaryMin, f.SalaryMax, f.SalaryCurrency, f.SalaryPeriod, f.SalaryEUR, f.ExperienceMin, f.ExperienceMax, f.OccupationID, f.SeniorityID}
}

type DescriptionDiagnostic struct {
	Sampled, RowExisted, UploadScheduled, UploadStateChanged bool
	OldHTMLChecksum, NewHTMLChecksum                         *string
}

func SaveContent(ctx context.Context, tx pgx.Tx, postingID string, fields ContentFields, description *DescriptionCandidate) (*DescriptionDiagnostic, error) {
	return saveContent(ctx, tx, postingID, fields, description, true)
}

func SaveEnrichment(ctx context.Context, tx pgx.Tx, postingID string, fields ContentFields, description *DescriptionCandidate) (*DescriptionDiagnostic, error) {
	return saveContent(ctx, tx, postingID, fields, description, false)
}

func saveContent(ctx context.Context, tx pgx.Tx, postingID string, fields ContentFields, description *DescriptionCandidate, requirePosting bool) (*DescriptionDiagnostic, error) {
	if !canonicalUUID.MatchString(postingID) {
		return nil, errors.New("invalid posting ID")
	}
	updated, err := tx.Exec(ctx, updateContentSQL, fields.args(postingID)...)
	if err != nil {
		return nil, err
	}
	if requirePosting && updated.RowsAffected() != 1 {
		return nil, fmt.Errorf("job_posting_not_found:%s", postingID)
	}
	var diagnostic *DescriptionDiagnostic
	if description != nil {
		diagnostic = &DescriptionDiagnostic{}
		err := tx.QueryRow(ctx, upsertDescriptionSQL, postingID, description.Locale, description.HTML, description.Hash, description.Hash).Scan(
			&diagnostic.Sampled, &diagnostic.RowExisted, &diagnostic.OldHTMLChecksum, &diagnostic.NewHTMLChecksum, &diagnostic.UploadScheduled, &diagnostic.UploadStateChanged)
		if err != nil {
			return nil, err
		}
	}
	_, err = tx.Exec(ctx, recordSuccessSQL, postingID)
	return diagnostic, err
}

type FailureDisposition string

const (
	FailureTransient FailureDisposition = "transient"
	FailureBudget    FailureDisposition = "budget"
	FailureGone      FailureDisposition = "gone"
)

func RecordFailure(ctx context.Context, tx pgx.Tx, postingID string, disposition FailureDisposition) error {
	if !canonicalUUID.MatchString(postingID) {
		return errors.New("invalid posting ID")
	}
	var err error
	switch disposition {
	case FailureTransient:
		_, err = tx.Exec(ctx, recordTransientSQL, postingID)
	case FailureBudget, FailureGone:
		_, err = tx.Exec(ctx, recordFailureSQL, postingID, disposition == FailureGone)
	default:
		return errors.New("unknown failure disposition")
	}
	return err
}

type Schedule struct {
	IsActive     bool
	NextScrapeAt *time.Time
}

func (s *Store) ReadSchedule(ctx context.Context, postingID string) (*Schedule, error) {
	if !canonicalUUID.MatchString(postingID) {
		return nil, errors.New("invalid posting ID")
	}
	result := &Schedule{}
	err := s.pool.QueryRow(ctx, "SELECT is_active, next_scrape_at FROM job_posting WHERE id = $1::uuid AND NOT tdm_reserved", postingID).Scan(&result.IsActive, &result.NextScrapeAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return result, err
}

func (s *Store) PostingReserved(ctx context.Context, postingID string) (bool, error) {
	if !canonicalUUID.MatchString(postingID) {
		return false, errors.New("invalid posting ID")
	}
	var reserved bool
	err := s.pool.QueryRow(ctx, "SELECT tdm_reserved FROM job_posting WHERE id = $1::uuid", postingID).Scan(&reserved)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	return reserved, err
}

// RecordReservation preserves listing visibility and schedules while marking
// the retained copy for existing downstream mining restrictions. The caller
// owns the same require/write/revoke transaction as all other posting effects.
func RecordReservation(ctx context.Context, tx pgx.Tx, postingID string, reservation *publisherpolicy.Reservation) error {
	if !canonicalUUID.MatchString(postingID) || reservation == nil || (reservation.Source != "header" && reservation.Source != "meta") {
		return errors.New("invalid reservation evidence")
	}
	evidence, err := json.Marshal(map[string]any{"url": reservation.URL, "source": reservation.Source, "policy_url": reservation.PolicyURL, "observed_at": time.Now().UTC().Format(time.RFC3339Nano)})
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true,tdm_reservation=$2::jsonb,updated_at=clock_timestamp() WHERE id=$1::uuid", postingID, string(evidence))
	return err
}
