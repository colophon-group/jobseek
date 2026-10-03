package queue

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const coldB0Version = "jobseek.crawler.cold-b0-target/v1"
const coldB0LuaSHA256 = "60bc7169651d3e8cc7abfcff6dec799170fa298539904a78b7f865534a3a2803"

var coldSafeID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
var coldB0Cohorts = map[string][]string{
	"c1":   {"browser-use-careers"},
	"c2":   {"browser-use-careers", "kandou-ai-careers"},
	"c3":   {"browser-use-careers", "eclypsium-careers", "kandou-ai-careers"},
	"cdom": {"algorized-careers", "browser-use-careers", "bunq-careers"},
}

type coldB0Board struct {
	ID           string `json:"board_id"`
	Slug         string `json:"board_slug"`
	ConfigSHA256 string `json:"config_sha256"`
}
type coldB0Document struct {
	Version   string        `json:"version"`
	Namespace string        `json:"namespace"`
	ShardID   string        `json:"shard_id"`
	Cohort    string        `json:"cohort"`
	Boards    []coldB0Board `json:"boards"`
}

// ColdB0Target is an immutable configuration/selector witness. It includes no
// adopted epoch, credentials or write grant. The host must verify the exact B0
// release/sentinel and install its producer/transferred tasks at the reservation
// epoch while all other writers remain cold. Publication audits that real queue.
type ColdB0Target struct {
	document          coldB0Document
	body, digest, lua string
}

func (t *ColdB0Target) SHA256() string {
	if t == nil {
		return ""
	}
	return t.digest
}
func (t *ColdB0Target) Payload() string {
	if t == nil {
		return ""
	}
	return t.body
}

func validColdB0(d coldB0Document) bool {
	allowed, ok := coldB0Cohorts[d.Cohort]
	if !ok || d.Version != coldB0Version || !coldSafeID.MatchString(d.Namespace) || !coldSafeID.MatchString(d.ShardID) || len(d.Boards) != len(allowed) {
		return false
	}
	ids := map[string]bool{}
	for i, b := range d.Boards {
		if b.Slug != allowed[i] || !canonicalUUID.MatchString(b.ID) || ids[b.ID] || !ownershipSHA256.MatchString(b.ConfigSHA256) {
			return false
		}
		ids[b.ID] = true
	}
	return true
}

// DecodeColdB0Target accepts only canonical content and the SAME reviewed B0 Lua
// used by the actual producer/supervisor. Arbitrary caller scripts cannot attest
// a queue. Epoch is supplied exclusively by the exact durable reservation.
func DecodeColdB0Target(body, digest string, lua []byte) (*ColdB0Target, error) {
	h := sha256.Sum256([]byte(body))
	l := sha256.Sum256(lua)
	if len(body) < 1 || len(body) > 16384 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest || hex.EncodeToString(l[:]) != coldB0LuaSHA256 {
		return nil, ErrConfiguration
	}
	var doc coldB0Document
	decoder := json.NewDecoder(strings.NewReader(body))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&doc) != nil || decoder.Decode(new(any)) != io.EOF || !validColdB0(doc) {
		return nil, ErrConfiguration
	}
	canonical, err := json.Marshal(doc)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return nil, ErrConfiguration
	}
	return &ColdB0Target{doc, body, digest, string(lua)}, nil
}

// Parse objects recursively so duplicate Redis metadata keys cannot hide drift
// behind JSON's last-key interpretation. Retain exact JSON numeric semantics.
func coldJSONValue(d *json.Decoder, depth int) (any, error) {
	if depth > 64 {
		return nil, ErrAuthorityLost
	}
	token, err := d.Token()
	if err != nil {
		return nil, ErrAuthorityLost
	}
	if delimiter, ok := token.(json.Delim); ok {
		switch delimiter {
		case '{':
			object := map[string]any{}
			for d.More() {
				key, err := d.Token()
				name, ok := key.(string)
				if err != nil || !ok {
					return nil, ErrAuthorityLost
				}
				if _, exists := object[name]; exists {
					return nil, ErrAuthorityLost
				}
				value, err := coldJSONValue(d, depth+1)
				if err != nil {
					return nil, err
				}
				object[name] = value
			}
			end, err := d.Token()
			if err != nil || end != json.Delim('}') {
				return nil, ErrAuthorityLost
			}
			return object, nil
		case '[':
			values := []any{}
			for d.More() {
				value, err := coldJSONValue(d, depth+1)
				if err != nil {
					return nil, err
				}
				values = append(values, value)
			}
			end, err := d.Token()
			if err != nil || end != json.Delim(']') {
				return nil, ErrAuthorityLost
			}
			return values, nil
		default:
			return nil, ErrAuthorityLost
		}
	}
	if n, ok := token.(json.Number); ok {
		return normalizeColdNumber(string(n))
	}
	return token, nil
}

// Compare mathematical JSON numbers without float rounding or unbounded decimal
// expansion. The same contract is exercised by the legacy guard's shared corpus.
// Exponents are bounded independently of coefficient precision and input size.
func normalizeColdNumber(raw string) (json.Number, error) {
	negative := strings.HasPrefix(raw, "-")
	if negative {
		raw = raw[1:]
	}
	exponent := int64(0)
	if i := strings.IndexAny(raw, "eE"); i >= 0 {
		var err error
		exponent, err = strconv.ParseInt(raw[i+1:], 10, 32)
		if err != nil || exponent < -1000000 || exponent > 1000000 {
			return "", ErrAuthorityLost
		}
		raw = raw[:i]
	}
	if i := strings.IndexByte(raw, '.'); i >= 0 {
		exponent -= int64(len(raw) - i - 1)
		raw = raw[:i] + raw[i+1:]
	}
	digits := strings.TrimLeft(raw, "0")
	if digits == "" {
		return json.Number("0"), nil
	}
	exponent += int64(len(digits)) - 1
	if exponent < -1000000 || exponent > 1000000 {
		return "", ErrAuthorityLost
	}
	digits = strings.TrimRight(digits, "0")
	var body string
	if exponent >= -6 && exponent < 21 {
		position := int(exponent) + 1
		switch {
		case position <= 0:
			body = "0." + strings.Repeat("0", -position) + digits
		case position >= len(digits):
			body = digits + strings.Repeat("0", position-len(digits))
		default:
			body = digits[:position] + "." + digits[position:]
		}
	} else {
		body = digits[:1]
		if len(digits) > 1 {
			body += "." + digits[1:]
		}
		body += "e" + strconv.FormatInt(exponent, 10)
	}
	if negative {
		body = "-" + body
	}
	return json.Number(body), nil
}
func coldMetadata(raw string) (string, error) {
	if len(raw) == 0 || len(raw) > 1<<20 {
		return "", ErrAuthorityLost
	}
	d := json.NewDecoder(strings.NewReader(raw))
	d.UseNumber()
	value, err := coldJSONValue(d, 0)
	if err != nil {
		return "", err
	}
	if _, ok := value.(map[string]any); !ok {
		return "", ErrAuthorityLost
	}
	if _, err := d.Token(); err != io.EOF {
		return "", ErrAuthorityLost
	}
	body, err := json.Marshal(value)
	if err != nil {
		return "", ErrAuthorityLost
	}
	return string(body), nil
}

func observeColdB0Board(ctx context.Context, tx pgx.Tx, c *Client, slug string) (coldB0Board, error) {
	var id, company, boardURL, kind, metadata, check, scrape, throttle string
	var monitorBrowser, scraperBrowser bool
	err := tx.QueryRow(ctx, `SELECT id::text,company_id::text,board_url,crawler_type,COALESCE(metadata,'{}'::jsonb)::text,
 check_interval_minutes::text,scrape_interval_hours::text,COALESCE(throttle_key,''),monitor_needs_browser,scraper_needs_browser
 FROM public.job_board WHERE board_slug=$1 AND is_enabled AND board_status='active' FOR SHARE`, slug).Scan(&id, &company, &boardURL, &kind, &metadata, &check, &scrape, &throttle, &monitorBrowser, &scraperBrowser)
	if errors.Is(err, pgx.ErrNoRows) {
		return coldB0Board{}, ErrAuthorityLost
	}
	if err != nil {
		return coldB0Board{}, err
	}
	u, err := url.Parse(boardURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || !scraperBrowser {
		return coldB0Board{}, ErrAuthorityLost
	}
	metadata, err = coldMetadata(metadata)
	if err != nil {
		return coldB0Board{}, err
	}
	domain := u.Hostname()
	if throttle != "" {
		domain = throttle
	}
	flag := func(b bool) string {
		if b {
			return "1"
		}
		return "0"
	}
	canonical := map[string]string{"board_slug": slug, "company_id": company, "board_url": boardURL, "crawler_type": kind, "metadata": metadata,
		"check_interval_minutes": check, "scrape_interval_hours": scrape, "throttle_key": throttle, "domain": domain, "monitor_needs_browser": flag(monitorBrowser), "scraper_needs_browser": flag(scraperBrowser)}
	cached, err := c.redis.HGetAll(ctx, "board:"+id).Result()
	if err != nil {
		return coldB0Board{}, ErrObservation
	}
	cachedMetadata, err := coldMetadata(cached["metadata"])
	if err != nil {
		return coldB0Board{}, err
	}
	cached["metadata"] = cachedMetadata
	for k, v := range canonical {
		if cached[k] != v {
			return coldB0Board{}, ErrAuthorityLost
		}
	}
	return coldB0Board{id, slug, configDigest(canonical)}, nil
}

func (t *ColdB0Target) attest(ctx context.Context, tx pgx.Tx, c *Client, p *OwnershipPlan) error {
	if t == nil || p == nil {
		return ErrConfiguration
	}
	ordinary := map[string]bool{}
	for _, m := range p.document.Members {
		ordinary[m.BoardID] = true
	}
	for _, expected := range t.document.Boards {
		if ordinary[expected.ID] {
			return ErrAuthorityLost
		}
		actual, err := observeColdB0Board(ctx, tx, c, expected.Slug)
		if err != nil {
			return err
		}
		if actual != expected {
			return ErrAuthorityLost
		}
	}
	return nil
}

// CaptureColdB0Target prepares the exact current PG/Redis cohort before intent.
// Runtime/source/image/sentinel attestations belong to the protected host wrapper.
func CaptureColdB0Target(ctx context.Context, pool *pgxpool.Pool, c *Client, epoch int64, namespace, shard, cohort string, lua []byte) (*ColdB0Target, error) {
	allowed, ok := coldB0Cohorts[cohort]
	if c == nil || !ok || epoch < 1 || epoch > 9999999999999 || !coldSafeID.MatchString(namespace) || !coldSafeID.MatchString(shard) {
		return nil, ErrConfiguration
	}
	doc := coldB0Document{coldB0Version, namespace, shard, cohort, nil}
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		var current int64
		var called bool
		if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM lightpanda_b0_routing_epoch_seq").Scan(&current, &called); err != nil {
			return err
		}
		if !called || current != epoch {
			return ErrAuthorityLost
		}
		for _, slug := range allowed {
			b, err := observeColdB0Board(ctx, tx, c, slug)
			if err != nil {
				return err
			}
			doc.Boards = append(doc.Boards, b)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(doc)
	if err != nil {
		return nil, ErrConfiguration
	}
	hash := sha256.Sum256(encoded)
	return DecodeColdB0Target(string(encoded), hex.EncodeToString(hash[:]), lua)
}

func (t *ColdB0Target) keys() []string {
	prefix := "lightpanda-b0:{" + t.document.Namespace + "}:"
	keys := []string{}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		keys = append(keys, prefix+suffix)
	}
	return keys
}
func (t *ColdB0Target) auditArguments(epoch int64) []any {
	args := []any{"audit", t.document.ShardID, number(float64(epoch)), "go", "", "0", "", "0", "0", "0", "", "", "", "64", "2.0", "", "0", t.document.Namespace, "", "0", t.document.Cohort, len(t.document.Boards), "0"}
	for _, b := range t.document.Boards {
		args = append(args, b.Slug)
	}
	return args
}
