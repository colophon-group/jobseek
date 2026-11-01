// Read-only, bounded Jobseek coverage projection and immutable private artifact.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const sourceCoverageVersion = "jobseek-coverage-v1"
const sourceCoverageWindow = 14 * 24 * time.Hour
const sourceCoverageGrace = 15 * time.Minute
const sourceCoverageMaxCadence = 24 * time.Hour
const sourceCoverageCheckLimit = 4096
const sourceCoverageSnapshotLimit = 4 << 20
const sourceCoverageArtifactLimit = 6 << 20

var sourceCoverageSlug = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,79}$`)
var sourceCoverageHost = regexp.MustCompile(`^[a-z0-9.-]+\.[a-z]{2,}$`)
var sourceCoverageHostLabel = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`)

type coverageCompany struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	URL  string `json:"url"`
}
type coverageInterval struct {
	Start string `json:"start"`
	End   string `json:"end"`
	State string `json:"state"`
}
type coverageScope struct {
	ID             string             `json:"id"`
	CompanyID      string             `json:"companyId"`
	Revision       string             `json:"revision"`
	TrackedSince   string             `json:"trackedSince"`
	LastVerifiedAt *string            `json:"lastVerifiedAt"`
	Coverage       []coverageInterval `json:"coverage"`
}
type coveragePosting struct {
	ID            string `json:"id"`
	ScopeID       string `json:"scopeId"`
	FirstObserved string `json:"firstObserved"`
}
type coverageSnapshot struct {
	SchemaVersion int               `json:"schemaVersion"`
	SourceVersion string            `json:"sourceVersion"`
	SnapshotID    string            `json:"snapshotId"`
	AsOf          string            `json:"asOf"`
	GeneratedAt   string            `json:"generatedAt"`
	Companies     []coverageCompany `json:"companies"`
	Scopes        []coverageScope   `json:"scopes"`
	Postings      []coveragePosting `json:"postings"`
}
type coverageArtifact struct {
	ArtifactVersion     int    `json:"artifactVersion"`
	SnapshotBytesBase64 string `json:"snapshotBytesBase64"`
	SHA256              string `json:"sha256"`
}
type coverageCheck struct {
	Revision string
	Started  time.Time
	Finished time.Time
	NextDue  time.Time
	Outcome  string
}
type coverageBoard struct {
	ID          string
	CompanyID   string
	Revision    string
	Status      string
	Tracked     time.Time
	Enabled     bool
	Fingerprint bool
}

func coverageTime(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05.000Z") }

func runSourceCoverageExport(args []string) error {
	if len(args) != 3 {
		return errors.New("usage: --export-source-coverage YYYY-MM-DD approved-company-slugs.txt private-output-directory")
	}
	end, err := time.Parse("2006-01-02", args[0])
	if err != nil || end.Weekday() != time.Monday {
		return errors.New("period end must be a Monday UTC date")
	}
	now := time.Now().UTC()
	if now.Before(end) || !now.Before(end.Add(24*time.Hour)) {
		return errors.New("export is allowed only within 24 hours after the Monday UTC period end")
	}
	approved, err := readCoverageAllowlist(args[1])
	if err != nil {
		return err
	}
	if len(approved) == 0 || len(approved) > 500 {
		return errors.New("approved company set must contain 1 to 500 companies")
	}
	if err := validateCoverageDirectory(args[2]); err != nil {
		return err
	}
	path := filepath.Join(args[2], args[0]+".json")
	if existing, err := readExistingCoverageArtifact(path); err == nil {
		return validateExistingCoverageArtifact(existing, end)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	dsn := os.Getenv("LOCAL_DATABASE_URL")
	if dsn == "" {
		return errors.New("LOCAL_DATABASE_URL is required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return err
	}
	defer pool.Close()
	tx, err := pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	snapshot, err := buildCoverageSnapshot(ctx, tx, end, now, approved)
	if err != nil {
		return err
	}
	if err := tx.Commit(ctx); err != nil {
		return err
	}
	bytes, err := json.Marshal(snapshot)
	if err != nil {
		return err
	}
	if len(bytes) > sourceCoverageSnapshotLimit {
		return fmt.Errorf("coverage snapshot exceeds %d bytes", sourceCoverageSnapshotLimit)
	}
	digest := sha256.Sum256(bytes)
	artifact, err := json.Marshal(coverageArtifact{1, base64.StdEncoding.EncodeToString(bytes), hex.EncodeToString(digest[:])})
	if err != nil {
		return err
	}
	if len(artifact) > sourceCoverageArtifactLimit {
		return fmt.Errorf("coverage artifact exceeds %d bytes", sourceCoverageArtifactLimit)
	}
	return publishCoverageArtifact(path, artifact, end)
}

func readCoverageAllowlist(path string) ([]string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(data) > 64*1024 {
		return nil, errors.New("approved company list is too large")
	}
	seen := make(map[string]bool)
	slugs := make([]string, 0)
	for line, raw := range strings.Split(string(data), "\n") {
		slug := strings.TrimSpace(raw)
		if slug == "" || strings.HasPrefix(slug, "#") {
			continue
		}
		if !sourceCoverageSlug.MatchString(slug) || seen[slug] {
			return nil, fmt.Errorf("invalid or duplicate approved company slug at line %d", line+1)
		}
		seen[slug] = true
		slugs = append(slugs, slug)
	}
	sort.Strings(slugs)
	return slugs, nil
}

func buildCoverageSnapshot(ctx context.Context, tx pgx.Tx, end, now time.Time, approved []string) (coverageSnapshot, error) {
	start := end.Add(-sourceCoverageWindow)
	s := coverageSnapshot{SchemaVersion: 1, SourceVersion: sourceCoverageVersion, AsOf: coverageTime(end), GeneratedAt: coverageTime(now), Companies: []coverageCompany{}, Scopes: []coverageScope{}, Postings: []coveragePosting{}}
	companyIDs := make(map[string]bool)
	rows, err := tx.Query(ctx, `SELECT id::text,name,website FROM public.company WHERE slug=ANY($1::text[]) ORDER BY id`, approved)
	if err != nil {
		return s, err
	}
	for rows.Next() {
		var c coverageCompany
		var website *string
		if err = rows.Scan(&c.ID, &c.Name, &website); err != nil {
			break
		}
		if website == nil {
			err = fmt.Errorf("approved company %s has no public HTTPS homepage", c.ID)
			break
		}
		c.URL, err = canonicalCoverageURL(*website)
		if err != nil {
			break
		}
		if !validCoverageCompanyName(c.Name) {
			err = fmt.Errorf("approved company %s has no valid public name", c.ID)
			break
		}
		companyIDs[c.ID] = true
		s.Companies = append(s.Companies, c)
	}
	rows.Close()
	if err != nil {
		return s, err
	}
	if err = rows.Err(); err != nil {
		return s, err
	}
	if len(s.Companies) != len(approved) {
		return s, errors.New("approved company list contains an unknown company")
	}
	var lostScopes int
	if err := tx.QueryRow(ctx, `SELECT count(*) FROM public.source_coverage_scope_loss
WHERE company_id::text=ANY($1::text[]) AND lost_at >= $2 AND lost_at <= $3`, companyIDList(companyIDs), start, now).Scan(&lostScopes); err != nil {
		return s, err
	}
	if lostScopes > 0 {
		return s, fmt.Errorf("%d approved career-page scopes were deleted or reassigned during the comparison window", lostScopes)
	}
	boardRows, err := tx.Query(ctx, `SELECT b.id::text,b.company_id::text,COALESCE(sc.revision::text,''),COALESCE(sc.tracked_since,'epoch'::timestamptz),b.is_enabled,b.board_status,
  NULLIF(b.metadata->>'_monitor_config_fingerprint','') IS NOT NULL
FROM public.job_board b LEFT JOIN public.source_coverage_scope sc ON sc.board_id=b.id
WHERE b.company_id::text=ANY($1::text[]) ORDER BY b.id`, companyIDList(companyIDs))
	if err != nil {
		return s, err
	}
	boards := []coverageBoard{}
	for boardRows.Next() {
		var b coverageBoard
		if err = boardRows.Scan(&b.ID, &b.CompanyID, &b.Revision, &b.Tracked, &b.Enabled, &b.Status, &b.Fingerprint); err != nil {
			break
		}
		if b.Revision == "" {
			err = errors.New("approved board lacks source coverage scope")
			break
		}
		boards = append(boards, b)
		if len(boards) > 2000 {
			err = errors.New("approved companies exceed 2000 scopes")
			break
		}
	}
	boardRows.Close()
	if err != nil {
		return s, err
	}
	if err = boardRows.Err(); err != nil {
		return s, err
	}
	for _, b := range boards {
		state, last, checkErr := readScopeCoverage(ctx, tx, b.ID, b.Revision, b.Tracked, start, end, now, b.Enabled && b.Status == "active" && b.Fingerprint)
		if checkErr != nil {
			err = checkErr
			break
		}
		s.Scopes = append(s.Scopes, coverageScope{ID: b.ID, CompanyID: b.CompanyID, Revision: b.Revision, TrackedSince: coverageTime(b.Tracked), LastVerifiedAt: last, Coverage: []coverageInterval{{Start: coverageTime(start), End: coverageTime(end), State: state}}})
	}
	if err != nil {
		return s, err
	}
	// The immutable insertion receipt, not mutable first_seen_at, determines
	// when a posting was first observed. Null, deleted, or reassigned board
	// identity is an export error even when the posting is already closed.
	postRows, err := tx.Query(ctx, `SELECT o.posting_id::text,o.board_id::text,o.first_observed_at,
 COALESCE(p.company_id=o.company_id AND p.board_id=o.board_id
  AND b.company_id=o.company_id AND o.revision IS NOT NULL,false)
FROM public.source_coverage_posting_first o
LEFT JOIN public.job_posting p ON p.id=o.posting_id
LEFT JOIN public.job_board b ON b.id=o.board_id
WHERE (o.company_id::text=ANY($1::text[]) OR p.company_id::text=ANY($1::text[]))
  AND o.first_observed_at >= $2 AND o.first_observed_at < $3
ORDER BY o.posting_id`, companyIDList(companyIDs), start, end)
	if err != nil {
		return s, err
	}
	for postRows.Next() {
		var p coveragePosting
		var first time.Time
		var boardID *string
		var provenance bool
		if err = postRows.Scan(&p.ID, &boardID, &first, &provenance); err != nil {
			break
		}
		if boardID == nil || !provenance {
			err = errors.New("selected posting observation has reassigned or missing board provenance")
			break
		}
		p.ScopeID = *boardID
		p.FirstObserved = coverageTime(first)
		s.Postings = append(s.Postings, p)
		if len(s.Postings) > 100000 {
			err = errors.New("approved companies exceed 100000 posting observations")
			break
		}
	}
	postRows.Close()
	if err != nil {
		return s, err
	}
	if err = postRows.Err(); err != nil {
		return s, err
	}
	identity, err := json.Marshal(struct {
		AsOf      string            `json:"asOf"`
		Companies []coverageCompany `json:"companies"`
		Scopes    []coverageScope   `json:"scopes"`
		Postings  []coveragePosting `json:"postings"`
	}{s.AsOf, s.Companies, s.Scopes, s.Postings})
	if err != nil {
		return s, err
	}
	h := sha256.Sum256(identity)
	s.SnapshotID = fmt.Sprintf("coverage-%s-%x", end.Format("20060102"), h[:12])
	return s, nil
}

func companyIDList(ids map[string]bool) []string {
	result := make([]string, 0, len(ids))
	for id := range ids {
		result = append(result, id)
	}
	sort.Strings(result)
	return result
}

func readScopeCoverage(ctx context.Context, tx pgx.Tx, id, revision string, tracked, start, end, now time.Time, eligible bool) (string, *string, error) {
	var previous *coverageCheck
	var p coverageCheck
	err := tx.QueryRow(ctx, `SELECT revision::text,started_at,finished_at,next_due_at,outcome FROM public.source_coverage_check
WHERE board_id=$1::uuid AND finished_at<$2 ORDER BY finished_at DESC LIMIT 1`, id, start).Scan(&p.Revision, &p.Started, &p.Finished, &p.NextDue, &p.Outcome)
	if err == nil {
		previous = &p
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return "", nil, err
	}
	rows, err := tx.Query(ctx, `SELECT revision::text,started_at,finished_at,next_due_at,outcome FROM public.source_coverage_check
WHERE board_id=$1::uuid AND finished_at >= $2 AND finished_at <= $3 ORDER BY finished_at,id LIMIT 4097`, id, start, now)
	if err != nil {
		return "", nil, err
	}
	checks := []coverageCheck{}
	for rows.Next() {
		var c coverageCheck
		if err = rows.Scan(&c.Revision, &c.Started, &c.Finished, &c.NextDue, &c.Outcome); err != nil {
			break
		}
		checks = append(checks, c)
		if len(checks) > sourceCoverageCheckLimit {
			err = errors.New("coverage checks exceed per-scope export limit")
			break
		}
	}
	rows.Close()
	if err != nil {
		return "", nil, err
	}
	if err = rows.Err(); err != nil {
		return "", nil, err
	}
	state, last := evaluateScopeCoverage(previous, checks, revision, tracked, start, end, eligible)
	return state, last, nil
}

func evaluateScopeCoverage(previous *coverageCheck, checks []coverageCheck, revision string, tracked, start, end time.Time, eligible bool) (string, *string) {
	var last *string
	if previous != nil && previous.Revision == revision && previous.Outcome == "complete" {
		value := coverageTime(previous.Finished)
		last = &value
	}
	for _, c := range checks {
		if c.Revision == revision && c.Outcome == "complete" {
			value := coverageTime(c.Finished)
			last = &value
		}
	}
	if !eligible || !tracked.Before(start) || previous == nil || previous.Revision != revision || previous.Outcome != "complete" || previous.NextDue.Before(start) || !previous.NextDue.After(previous.Finished) || previous.NextDue.Sub(previous.Finished) > sourceCoverageMaxCadence {
		return "unknown", last
	}
	prior := *previous
	interrupted := false
	reachedEnd := false
	for _, c := range checks {
		if c.Revision != revision || c.Outcome != "complete" || c.Started.After(prior.NextDue.Add(sourceCoverageGrace)) || c.Finished.Before(prior.Finished) || !c.NextDue.After(c.Finished) || c.NextDue.Sub(c.Finished) > sourceCoverageMaxCadence {
			interrupted = true
		}
		if c.Finished.After(end) {
			reachedEnd = true
			break
		}
		prior = c
	}
	if !reachedEnd {
		return "unknown", last
	}
	if interrupted {
		return "interrupted", last
	}
	return "complete", last
}

func canonicalCoverageURL(raw string) (string, error) {
	u, err := url.Parse(raw)
	if err != nil || len(raw) > 2048 || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || (u.Port() != "" && u.Port() != "443") || u.RawQuery != "" || u.Fragment != "" || u.Opaque != "" || strings.ContainsAny(raw, "?#\\") {
		return "", errors.New("approved company has no valid public HTTPS homepage")
	}
	host := strings.ToLower(u.Hostname())
	if len(host) > 253 || net.ParseIP(host) != nil || !sourceCoverageHost.MatchString(host) || privateCoverageHost(host) {
		return "", errors.New("approved company has no valid public HTTPS homepage")
	}
	for _, part := range strings.Split(host, ".") {
		if len(part) > 63 || !sourceCoverageHostLabel.MatchString(part) {
			return "", errors.New("approved company has no valid public HTTPS homepage")
		}
	}
	u.Host = host
	if u.Path == "" {
		u.Path = "/"
	}
	for _, segment := range strings.Split(u.Path, "/") {
		if segment == "." || segment == ".." {
			return "", errors.New("approved company has no valid public HTTPS homepage")
		}
	}
	for _, r := range u.Path {
		if r < 0x20 || r == 0x7f {
			return "", errors.New("approved company has no valid public HTTPS homepage")
		}
	}
	return u.String(), nil
}

func privateCoverageHost(host string) bool {
	for _, suffix := range []string{"localhost", "local", "internal", "invalid", "test", "lan", "home", "corp"} {
		if host == suffix || strings.HasSuffix(host, "."+suffix) {
			return true
		}
	}
	return false
}

func validCoverageCompanyName(name string) bool {
	if strings.TrimSpace(name) == "" || len(name) > 200 {
		return false
	}
	for _, r := range name {
		if r < 0x20 || r == 0x7f || r == '<' || r == '>' {
			return false
		}
	}
	return true
}

func validateCoverageDirectory(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0077 != 0 {
		return errors.New("coverage output directory must be a private real directory")
	}
	return nil
}

func readExistingCoverageArtifact(path string) ([]byte, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > sourceCoverageArtifactLimit {
		return nil, errors.New("existing coverage artifact is not a bounded private regular file")
	}
	return io.ReadAll(io.LimitReader(f, sourceCoverageArtifactLimit+1))
}

func validateExistingCoverageArtifact(data []byte, end time.Time) error {
	if len(data) > sourceCoverageArtifactLimit {
		return errors.New("existing coverage artifact exceeds size limit")
	}
	var wrapper coverageArtifact
	if err := json.Unmarshal(data, &wrapper); err != nil {
		return err
	}
	if wrapper.ArtifactVersion != 1 {
		return errors.New("existing coverage artifact has unsupported version")
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(wrapper.SnapshotBytesBase64)
	if err != nil {
		return err
	}
	if len(decoded) > sourceCoverageSnapshotLimit || base64.StdEncoding.EncodeToString(decoded) != wrapper.SnapshotBytesBase64 {
		return errors.New("existing coverage snapshot has invalid size or base64")
	}
	digest := sha256.Sum256(decoded)
	if hex.EncodeToString(digest[:]) != wrapper.SHA256 {
		return errors.New("existing coverage artifact digest mismatch")
	}
	var snapshot coverageSnapshot
	if err = json.Unmarshal(decoded, &snapshot); err != nil {
		return err
	}
	if snapshot.SchemaVersion != 1 || snapshot.SourceVersion != sourceCoverageVersion || snapshot.AsOf != coverageTime(end) {
		return errors.New("existing coverage artifact has wrong source version or period")
	}
	return nil
}

func publishCoverageArtifact(path string, data []byte, end time.Time) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".coverage-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err = tmp.Chmod(0600); err != nil {
		tmp.Close()
		return err
	}
	if _, err = tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	// Link publishes the fully synced sibling without replacing an immutable
	// period. A concurrent identical publisher may reuse the existing file.
	if err = os.Link(tmp.Name(), path); err != nil {
		if !errors.Is(err, os.ErrExist) {
			return err
		}
		existing, readErr := readExistingCoverageArtifact(path)
		if readErr != nil {
			return readErr
		}
		if string(existing) != string(data) {
			return errors.New("coverage period already has a different immutable artifact")
		}
		if err := validateExistingCoverageArtifact(existing, end); err != nil {
			return err
		}
		return syncCoverageDirectory(dir)
	}
	if err = os.Remove(tmp.Name()); err != nil {
		return err
	}
	return syncCoverageDirectory(dir)
}

func syncCoverageDirectory(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}
