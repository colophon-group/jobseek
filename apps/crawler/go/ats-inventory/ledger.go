package main

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"time"

	_ "modernc.org/sqlite"
)

type githubWorkItem struct {
	Kind      string   `json:"kind"`
	Number    int      `json:"number"`
	State     string   `json:"state"`
	Title     string   `json:"title"`
	Body      string   `json:"body"`
	URL       string   `json:"url"`
	Labels    []string `json:"labels"`
	CreatedAt *string  `json:"created_at"`
}

func (i githubWorkItem) reference() string {
	return fmt.Sprintf("%s:%d [%s] %s (%s)", i.Kind, i.Number, i.State, i.Title, i.URL)
}

type githubClaim struct {
	IssueNumber int    `json:"issue_number"`
	Body        string `json:"body"`
	CreatedAt   string `json:"created_at"`
}
type ledgerRecord struct {
	SourceKey     string   `json:"source_key"`
	NormalizedURL *string  `json:"normalized_url"`
	State         string   `json:"state"`
	References    []string `json:"references"`
}
type ledgerReconciliation struct {
	RemoteMarkers        int      `json:"remote_markers"`
	RecoveredSources     int      `json:"recovered_sources"`
	MissingRemoteSources []string `json:"missing_remote_sources"`
}
type candidateLedger struct {
	DB      *sql.DB
	Sources map[string]ledgerRecord
	URLs    map[string][]ledgerRecord
	Now     func() time.Time
}

const inventoryLedgerSchema = `
CREATE TABLE IF NOT EXISTS candidates (source_key TEXT PRIMARY KEY,source_hash TEXT NOT NULL UNIQUE,normalized_url TEXT,normalized_url_hash TEXT,family TEXT,tenant TEXT,state TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS candidates_url_hash ON candidates(normalized_url_hash);
CREATE TABLE IF NOT EXISTS remote_items (kind TEXT NOT NULL,number INTEGER NOT NULL,source_key TEXT NOT NULL REFERENCES candidates(source_key),state TEXT NOT NULL,url TEXT NOT NULL,title TEXT NOT NULL,board_url_hash TEXT NOT NULL,last_seen_at INTEGER NOT NULL,PRIMARY KEY(kind,number));
CREATE INDEX IF NOT EXISTS remote_items_source ON remote_items(source_key);
CREATE TABLE IF NOT EXISTS creation_events (issue_number INTEGER PRIMARY KEY,source_key TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS creation_events_created ON creation_events(created_at);
`

func openCandidateLedger(path string) (*candidateLedger, error) {
	if e := os.MkdirAll(filepath.Dir(path), 0755); e != nil {
		return nil, e
	}
	absolute, e := filepath.Abs(path)
	if e != nil {
		return nil, e
	}
	endpoint := url.URL{Scheme: "file", Path: absolute}
	q := endpoint.Query()
	q.Set("_txlock", "immediate")
	q.Add("_pragma", "busy_timeout(5000)")
	q.Add("_pragma", "journal_mode(WAL)")
	q.Add("_pragma", "foreign_keys(1)")
	endpoint.RawQuery = q.Encode()
	db, e := sql.Open("sqlite", endpoint.String())
	if e != nil {
		return nil, e
	}
	db.SetMaxOpenConns(1)
	fail := func(e error) (*candidateLedger, error) { db.Close(); return nil, e }
	if _, e = db.Exec(inventoryLedgerSchema); e != nil {
		return fail(e)
	}
	ledger := &candidateLedger{DB: db, Now: time.Now}
	if e = ledger.refreshIndex(); e != nil {
		return fail(e)
	}
	return ledger, nil
}
func (l *candidateLedger) Close() error { return l.DB.Close() }
func (l *candidateLedger) refreshIndex() error {
	rows, e := l.DB.Query("SELECT source_key, normalized_url,state,normalized_url_hash FROM candidates ORDER BY source_key")
	if e != nil {
		return e
	}
	type row struct {
		source           string
		normalized, hash sql.NullString
		state            string
	}
	records := []row{}
	for rows.Next() {
		var r row
		if e = rows.Scan(&r.source, &r.normalized, &r.state, &r.hash); e != nil {
			rows.Close()
			return e
		}
		records = append(records, r)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	rows, e = l.DB.Query("SELECT source_key,kind,number,state,url FROM remote_items ORDER BY source_key,kind,number")
	if e != nil {
		return e
	}
	refs := map[string][]string{}
	for rows.Next() {
		var source, kind, state, endpoint string
		var number int
		if e = rows.Scan(&source, &kind, &number, &state, &endpoint); e != nil {
			rows.Close()
			return e
		}
		refs[source] = append(refs[source], fmt.Sprintf("%s:%d [%s] %s", kind, number, state, endpoint))
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	sources := map[string]ledgerRecord{}
	urls := map[string][]ledgerRecord{}
	for _, r := range records {
		var normalized *string
		if r.normalized.Valid {
			normalized = inventoryStringPointer(r.normalized.String)
		}
		record := ledgerRecord{r.source, normalized, r.state, nonnilStrings(refs[r.source])}
		sources[r.source] = record
		if r.hash.Valid {
			urls[r.hash.String] = append(urls[r.hash.String], record)
		}
	}
	l.Sources, l.URLs = sources, urls
	return nil
}
func (l *candidateLedger) reconcileRemote(ctx context.Context, items []githubWorkItem) (ledgerReconciliation, error) {
	var empty ledgerReconciliation
	now := l.Now().Unix()
	tx, e := l.DB.BeginTx(ctx, nil)
	if e != nil {
		return empty, e
	}
	defer tx.Rollback()
	before := map[string]bool{}
	rows, e := tx.QueryContext(ctx, "SELECT source_key FROM candidates")
	if e != nil {
		return empty, e
	}
	for rows.Next() {
		var key string
		if e = rows.Scan(&key); e != nil {
			rows.Close()
			return empty, e
		}
		before[key] = true
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return empty, e
	}
	if _, e = tx.ExecContext(ctx, "DELETE FROM remote_items"); e != nil {
		return empty, e
	}
	observed := map[string]bool{}
	marked := 0
	for _, item := range items {
		if item.Kind != "issue" {
			continue
		}
		for _, marker := range parseCandidateMarkers(item.Body) {
			marked++
			observed[marker.SourceKey] = true
			state := "remote_" + item.Kind + "_" + unicodeFold(item.State)
			_, e = tx.ExecContext(ctx, `INSERT INTO candidates(source_key,source_hash,normalized_url,normalized_url_hash,family,tenant,state,created_at,updated_at) VALUES(?,?,NULL,?,NULL,NULL,?,?,?) ON CONFLICT(source_key) DO UPDATE SET normalized_url_hash=COALESCE(candidates.normalized_url_hash,excluded.normalized_url_hash),state=excluded.state,updated_at=excluded.updated_at`, marker.SourceKey, sha256Bytes([]byte(marker.SourceKey)), marker.BoardHash, state, now, now)
			if e != nil {
				return empty, e
			}
			if e = recordLedgerRemoteItem(ctx, tx, item, marker.SourceKey, marker.BoardHash, now); e != nil {
				return empty, e
			}
			if slices.Contains(item.Labels, atsInventoryLabel) && item.CreatedAt != nil {
				if at, e := parseInventoryTime(*item.CreatedAt); e == nil {
					if _, e = tx.ExecContext(ctx, "INSERT OR IGNORE INTO creation_events(issue_number,source_key,created_at) VALUES(?,?,?)", item.Number, marker.SourceKey, at.Unix()); e != nil {
						return empty, e
					}
				}
			}
		}
	}
	missing := []string{}
	recovered := 0
	for key := range before {
		if !observed[key] {
			missing = append(missing, key)
		}
	}
	sort.Strings(missing)
	for key := range observed {
		if !before[key] {
			recovered++
		}
	}
	for _, key := range missing {
		if _, e = tx.ExecContext(ctx, "UPDATE candidates SET state='remote_missing',updated_at=? WHERE source_key=?", now, key); e != nil {
			return empty, e
		}
	}
	if e = tx.Commit(); e != nil {
		return empty, e
	}
	if e = l.refreshIndex(); e != nil {
		return empty, e
	}
	return ledgerReconciliation{marked, recovered, missing}, nil
}
func recordLedgerRemoteItem(ctx context.Context, tx *sql.Tx, item githubWorkItem, source, boardHash string, now int64) error {
	_, e := tx.ExecContext(ctx, `INSERT INTO remote_items(kind,number,source_key,state,url,title,board_url_hash,last_seen_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(kind,number) DO UPDATE SET source_key=excluded.source_key,state=excluded.state,url=excluded.url,title=excluded.title,board_url_hash=excluded.board_url_hash,last_seen_at=excluded.last_seen_at`, item.Kind, item.Number, source, item.State, item.URL, item.Title, boardHash, now)
	return e
}
func (l *candidateLedger) recordCreated(ctx context.Context, candidate inventoryCandidate, item githubWorkItem) error {
	if item.Kind != "issue" {
		return fmt.Errorf("only created GitHub issues may enter durable ledger")
	}
	now := l.Now().Unix()
	boardHash := candidate.boardURLHash()
	for _, marker := range parseCandidateMarkers(item.Body) {
		if marker.SourceKey == candidate.SourceKey {
			boardHash = marker.BoardHash
			break
		}
	}
	tx, e := l.DB.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	_, e = tx.ExecContext(ctx, `INSERT INTO candidates(source_key,source_hash,normalized_url,normalized_url_hash,family,tenant,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET normalized_url=excluded.normalized_url,normalized_url_hash=excluded.normalized_url_hash,family=excluded.family,tenant=excluded.tenant,state=excluded.state,updated_at=excluded.updated_at`, candidate.SourceKey, candidate.sourceHash(), candidate.BoardURL, candidate.boardURLHash(), candidate.Family, candidate.Tenant, "remote_"+item.Kind+"_"+unicodeFold(item.State), now, now)
	if e != nil {
		return e
	}
	if e = recordLedgerRemoteItem(ctx, tx, item, candidate.SourceKey, boardHash, now); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, "INSERT OR IGNORE INTO creation_events(issue_number,source_key,created_at) VALUES(?,?,?)", item.Number, candidate.SourceKey, now); e != nil {
		return e
	}
	if e = tx.Commit(); e != nil {
		return e
	}
	return l.refreshIndex()
}
func (l *candidateLedger) countCreatedSince(ctx context.Context, at int64) (int, error) {
	var n int
	e := l.DB.QueryRowContext(ctx, "SELECT COUNT(*) FROM creation_events WHERE created_at>=?", at).Scan(&n)
	return n, e
}
func (l *candidateLedger) findSource(source string) (ledgerRecord, bool) {
	v, ok := l.Sources[source]
	return v, ok
}
func (l *candidateLedger) findURL(endpoint string) []ledgerRecord {
	return l.URLs[sha256Bytes([]byte(endpoint))]
}
