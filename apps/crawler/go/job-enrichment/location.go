package enrichment

// Location matching shares the bounded SQLite index used during transition.
// There is no network or PostgreSQL connection in this package. A native worker
// can use the same resolver with an index loaded by its existing database pool.
import (
	"database/sql"
	_ "embed"
	"encoding/json"
	"fmt"
	"net/url"
	"sort"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	rx "github.com/dlclark/regexp2/v2"
	"golang.org/x/text/unicode/norm"
	_ "modernc.org/sqlite"
)

//go:embed location_rules.json
var locationRulesJSON []byte
var locationConfig struct {
	Maps     map[string]map[string]string `json:"maps"`
	Digits   [][2]rune                    `json:"digits"`
	Patterns map[string]salaryPattern     `json:"patterns"`
}
var locationPatterns = map[string]*rx.Regexp{}

func init() {
	if err := json.Unmarshal(locationRulesJSON, &locationConfig); err != nil {
		panic(err)
	}
	for key, rule := range locationConfig.Patterns {
		locationPatterns[key] = salaryRegex(rule.Pattern, rule.IgnoreCase)
	}
}

type LocationResult struct {
	ID   *int64 `json:"location_id"`
	Type string `json:"location_type"`
}
type LocationMiss struct {
	Raw    string `json:"raw_value"`
	Sample string `json:"sample_value"`
}
type locationEntry struct {
	id         int64
	parent     *int64
	kind       string
	population int64
	languages  []string
}
type LocationResolver struct {
	deadline time.Time
	db       *sql.DB
	err      error
	// Cache exists only for one operation, never a second complete GeoNames index.
	entries        map[int64]*locationEntry
	misses         map[string]bool
	negative       map[string]bool
	tracking       bool
	language       string
	locationMisses []LocationMiss
}

func OpenLocations(path string) (*LocationResolver, error) {
	dsn := (&url.URL{Scheme: "file", Path: path}).String() + "?mode=ro&_pragma=busy_timeout(5000)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	if err = db.Ping(); err != nil {
		db.Close()
		return nil, err
	}
	r := &LocationResolver{db: db}
	r.reset()
	// Missing or partially loaded indexes must never return empty success.
	var count int
	if err = db.QueryRow("SELECT COUNT(*) FROM entry").Scan(&count); err != nil {
		db.Close()
		return nil, err
	}
	return r, nil
}
func (r *LocationResolver) Close() error { return r.db.Close() }
func (r *LocationResolver) reset() {
	r.deadline = time.Now().Add(5 * time.Second)
	r.err = nil
	r.entries = map[int64]*locationEntry{}
	r.misses = map[string]bool{}
	r.locationMisses = []LocationMiss{}
}
func (r *LocationResolver) entry(id int64) *locationEntry {
	if !r.checkDeadline() {
		return nil
	}
	if e, ok := r.entries[id]; ok {
		return e
	}
	e := &locationEntry{id: id}
	var langs string
	err := r.db.QueryRow("SELECT parent_id,loc_type,population,languages FROM entry WHERE id=?", id).Scan(&e.parent, &e.kind, &e.population, &langs)
	if err == sql.ErrNoRows {
		r.entries[id] = nil
		return nil
	}
	if err != nil {
		r.err = err
		return nil
	}
	if langs != "" {
		e.languages = strings.Split(langs, ",")
	}
	r.entries[id] = e
	return e
}
func (r *LocationResolver) lookup(key string) []int64 {
	if !r.checkDeadline() {
		return []int64{}
	}
	ids := []int64{}
	rows, err := r.db.Query("SELECT location_id FROM name_index WHERE name=?", key)
	if err != nil {
		r.err = err
		return ids
	}
	for rows.Next() {
		var id int64
		if err = rows.Scan(&id); err != nil {
			r.err = err
			break
		}
		ids = append(ids, id)
	}
	if err = rows.Err(); err != nil {
		r.err = err
	}
	rows.Close()
	if len(ids) == 0 && r.tracking && key != "" && !r.negative[key] {
		r.misses[key] = true
	}
	return ids
}
func locationStripAccents(s string) string {
	return strings.Map(func(c rune) rune {
		if c >= 0x300 && c <= 0x36f {
			return -1
		}
		return c
	}, norm.NFD.String(s))
}
func LocationNameVariants(name string) []string {
	out := []string{name}
	stripped := locationStripAccents(name)
	bases := []string{name}
	if stripped != name {
		out = append(out, stripped)
		bases = append(bases, stripped)
	}
	for _, b := range bases {
		if strings.HasPrefix(b, "the ") {
			out = append(out, b[4:])
		}
		if strings.HasSuffix(b, " city") {
			out = append(out, b[:len(b)-5])
		}
	}
	return out
}
func (r *LocationResolver) regex(name, text string) *rx.Match {
	if !r.checkDeadline() {
		return nil
	}
	m, err := locationPatterns[name].FindStringMatch(text)
	if err != nil {
		r.err = err
	}
	return m
}
func (r *LocationResolver) replace(name, text string) string {
	if !r.checkDeadline() {
		return text
	}
	s, err := locationPatterns[name].Replace(text, "", 0, -1)
	if err != nil {
		r.err = err
	}
	return s
}
func locationTrim(s string) string        { return strings.TrimFunc(s, space) }
func locationMap(name, key string) string { return locationConfig.Maps[name][key] }
func normalizeLocationType(s string) string {
	return locationMap("_JOB_LOCATION_TYPE_MAP", lower(locationTrim(s)))
}

// NormalizeJobLocationType shares the existing canonical enum map with provider
// parsers, including the Python parenthetical-qualifier form. Empty means unknown.
func NormalizeJobLocationType(raw string) string {
	key := lower(locationTrim(raw))
	if value := locationMap("_JOB_LOCATION_TYPE_MAP", key); value != "" {
		return value
	}
	if strings.HasSuffix(key, ")") {
		if index := strings.Index(key, " ("); index >= 0 {
			return locationMap("_JOB_LOCATION_TYPE_MAP", key[:index])
		}
	}
	return ""
}
func ptrID(id int64) *int64 {
	if id == 0 {
		return nil
	}
	return &id
}
func idList(id int64) []int64 {
	if id == 0 {
		return []int64{}
	}
	return []int64{id}
}
func idSet(ids []int64) map[int64]bool {
	out := map[int64]bool{}
	for _, id := range ids {
		out[id] = true
	}
	return out
}
func appendUnique(out []int64, id int64) []int64 {
	if id == 0 {
		return out
	}
	for _, old := range out {
		if old == id {
			return out
		}
	}
	return append(out, id)
}
func (r *LocationResolver) filter(ids []int64, kinds ...string) []int64 {
	out := []int64{}
	for _, id := range ids {
		e := r.entry(id)
		if e == nil {
			continue
		}
		for _, kind := range kinds {
			if e.kind == kind {
				out = append(out, id)
				break
			}
		}
	}
	return out
}
func (r *LocationResolver) descendant(id int64, ancestors map[int64]bool) bool {
	for depth := 0; depth < 5; depth++ {
		e := r.entry(id)
		if e == nil || e.parent == nil {
			return false
		}
		if ancestors[*e.parent] {
			return true
		}
		id = *e.parent
	}
	return false
}
func (r *LocationResolver) within(ids []int64, ancestors map[int64]bool) []int64 {
	out := []int64{}
	for _, id := range ids {
		if r.descendant(id, ancestors) {
			out = append(out, id)
		}
	}
	return out
}
func (r *LocationResolver) best(ids []int64) int64 {
	if len(ids) == 0 {
		return 0
	}
	if r.language != "" && len(ids) > 1 {
		matched := []int64{}
		for _, id := range ids {
			e := r.entry(id)
			if e != nil {
				for _, lang := range e.languages {
					if lang == r.language {
						matched = append(matched, id)
						break
					}
				}
			}
		}
		if len(matched) > 0 && len(matched) < len(ids) {
			ids = matched
		}
	}
	best := ids[0]
	population := int64(-1)
	rank := -1
	ranks := map[string]int{"city": 3, "region": 2, "country": 1, "macro": 0}
	for _, id := range ids {
		e := r.entry(id)
		if e == nil {
			continue
		}
		n := ranks[e.kind]
		if e.population > population || (e.population == population && n > rank) {
			best = id
			population = e.population
			rank = n
		}
	}
	return best
}
func (r *LocationResolver) population(id int64) int64 {
	e := r.entry(id)
	if e == nil {
		return 0
	}
	return e.population
}
func (r *LocationResolver) exact(text string) int64 {
	if alias := locationMap("_CITY_ALIASES", lower(text)); alias != "" {
		text = alias
	}
	key := lower(text)
	ids := r.lookup(key)
	if len(ids) == 0 {
		ids = r.lookup(locationStripAccents(key))
	}
	for _, pattern := range []string{"_THE_PREFIX_RE", "_CITY_SUFFIX_RE"} {
		if len(ids) == 0 {
			v := r.replace(pattern, key)
			if v != key {
				ids = r.lookup(v)
				if len(ids) == 0 {
					ids = r.lookup(locationStripAccents(v))
				}
			}
		}
	}
	if len(ids) == 0 {
		return 0
	}
	if len(ids) == 1 {
		return ids[0]
	}
	countries := r.filter(ids, "country")
	if len(countries) > 0 {
		return r.best(countries)
	}
	cities, regions := r.filter(ids, "city"), r.filter(ids, "region", "macro")
	if len(cities) > 0 && len(regions) > 0 {
		inside := r.within(cities, idSet(regions))
		br := r.best(regions)
		if len(inside) > 0 {
			bc := r.best(inside)
			pop := r.population(bc)
			if pop > 0 && pop*10 < r.population(br) {
				return br
			}
			return bc
		}
		bc := r.best(cities)
		if r.population(bc) >= r.population(br) {
			return bc
		}
		return br
	}
	if len(cities) > 0 {
		return r.best(cities)
	}
	return r.best(ids)
}
func (r *LocationResolver) single(token string) int64 {
	n := utf8.RuneCountInString(token)
	upper := strings.ToUpper(token)
	if n == 2 {
		if alias := locationMap("_CITY_ALIASES", lower(token)); alias != "" {
			if id := r.exact(alias); id != 0 {
				return id
			}
		}
		// Standalone collisions prefer the US state after the ISO-first full-string check.
		state, country := locationMap("_US_STATE_ABBREV", upper), locationMap("_ISO2_TO_COUNTRY", upper)
		if state != "" {
			if id := r.exact(state); id != 0 {
				return id
			}
		} else if country != "" {
			if id := r.exact(country); id != 0 {
				return id
			}
		}
	}
	if n == 3 {
		if country := locationMap("_ISO3_TO_COUNTRY", upper); country != "" {
			if id := r.exact(country); id != 0 {
				return id
			}
		}
	}
	return r.exact(token)
}
func (r *LocationResolver) tokenIDs(token string) []int64 {
	if alias := locationMap("_CITY_ALIASES", lower(token)); alias != "" {
		if ids := r.lookup(lower(alias)); len(ids) > 0 {
			return ids
		}
	}
	upper := strings.ToUpper(token)
	n := utf8.RuneCountInString(token)
	if n == 2 {
		state, country := locationMap("_US_STATE_ABBREV", upper), locationMap("_ISO2_TO_COUNTRY", upper)
		if state != "" || country != "" {
			ids := []int64{}
			if state != "" {
				ids = append(ids, r.lookup(lower(state))...)
			}
			if country != "" {
				existing := idSet(ids)
				for _, id := range r.lookup(lower(country)) {
					if !existing[id] {
						ids = append(ids, id)
					}
				}
			}
			return ids
		}
	}
	if n == 3 {
		if country := locationMap("_ISO3_TO_COUNTRY", upper); country != "" {
			if ids := r.lookup(lower(country)); len(ids) > 0 {
				return ids
			}
		}
	}
	ids := r.lookup(lower(token))
	if len(ids) == 0 {
		ids = r.lookup(locationStripAccents(lower(token)))
	}
	return ids
}
func (r *LocationResolver) target(ids []int64) int64 {
	cities, regions := r.filter(ids, "city"), r.filter(ids, "country", "region", "macro")
	if len(cities) > 0 && len(regions) > 0 {
		inside := r.within(cities, idSet(regions))
		if len(inside) > 0 {
			return r.best(inside)
		}
		return r.best(regions)
	}
	if len(cities) > 0 {
		return r.best(cities)
	}
	if len(regions) > 0 {
		return r.best(regions)
	}
	return r.best(ids)
}
func (r *LocationResolver) narrowest(order []int64) int64 {
	ids := locationSetOrder(order)
	best := int64(0)
	spec := -1
	pop := int64(-1)
	ranks := map[string]int{"region": 3, "country": 2, "macro": 1}
	for _, id := range ids {
		e := r.entry(id)
		if e == nil {
			continue
		}
		s := ranks[e.kind]
		if s > spec || (s == spec && e.population > pop) {
			best = id
			spec = s
			pop = e.population
		}
	}
	return best
}
func numericToken(s string) bool {
	if s == "" {
		return false
	}
	for _, c := range s {
		found := false
		for _, span := range locationConfig.Digits {
			if c >= span[0] && c <= span[1] {
				found = true
				break
			}
		}
		if !found {
			return false
		}
	}
	return true
}

func (r *LocationResolver) multi(tokens []string) []int64 {
	groups := [][]int64{}
	for _, t := range tokens {
		if !numericToken(t) {
			groups = append(groups, r.tokenIDs(t))
		}
	}
	if len(groups) == 0 {
		return []int64{}
	}
	ctx := map[int64]bool{}
	contextOrder := []int64{}
	end := len(groups)
	for i := len(groups) - 1; i > 0; i-- {
		ids := groups[i]
		if len(ids) == 0 {
			continue
		}
		contexts, cities := r.filter(ids, "country", "region", "macro"), r.filter(ids, "city")
		if len(ctx) == 0 {
			accept := len(contexts) > 0 && len(cities) == 0
			if len(contexts) > 0 && len(cities) > 0 {
				candidate := idSet(contexts)
				for _, left := range groups[:i] {
					if len(r.within(left, candidate)) > 0 {
						accept = true
						break
					}
				}
			}
			if accept {
				for _, id := range contexts {
					ctx[id] = true
					contextOrder = append(contextOrder, id)
				}
				end = i
				continue
			}
			break
		}
		narrowed := r.within(contexts, ctx)
		if len(narrowed) > 0 {
			for _, id := range narrowed {
				ctx[id] = true
				contextOrder = append(contextOrder, id)
			}
			end = i
			continue
		}
		break
	}
	if len(ctx) == 0 {
		pure := map[int64]bool{}
		targets := [][]int64{}
		for _, ids := range groups {
			if len(ids) == 0 {
				continue
			}
			contexts, cities := r.filter(ids, "country", "region", "macro"), r.filter(ids, "city")
			if len(contexts) > 0 && len(cities) == 0 {
				for _, id := range contexts {
					pure[id] = true
				}
			} else {
				targets = append(targets, ids)
			}
		}
		out := []int64{}
		if len(pure) > 0 && len(targets) > 0 {
			for _, ids := range targets {
				inside := r.within(ids, pure)
				if len(inside) > 0 {
					out = appendUnique(out, r.target(inside))
				} else {
					out = appendUnique(out, r.best(ids))
				}
			}
			if len(out) > 0 {
				return out
			}
		}
		for _, ids := range groups {
			if len(ids) > 0 {
				out = appendUnique(out, r.target(ids))
			}
		}
		return out
	}
	targets := []int64{}
	for _, ids := range groups[:end] {
		targets = append(targets, ids...)
	}
	nid := r.narrowest(contextOrder)
	if len(targets) == 0 {
		return idList(nid)
	}
	narrow := ctx
	if nid != 0 {
		narrow = idSet([]int64{nid})
	}
	inside := r.within(targets, narrow)
	if len(inside) == 0 {
		inside = r.within(targets, ctx)
	}
	if len(inside) > 0 {
		return idList(r.target(inside))
	}
	regions := r.within(r.filter(targets, "country", "region", "macro"), ctx)
	if len(regions) > 0 {
		return idList(r.best(regions))
	}
	return idList(nid)
}
func (r *LocationResolver) compound(words []string, depth int) int64 {
	for pos := len(words) - 1; pos > 0; pos-- {
		right := strings.Join(words[pos:], " ")
		ids := r.tokenIDs(right)
		if len(ids) == 0 {
			ids = r.lookup(lower(right))
			if len(ids) == 0 {
				ids = r.lookup(locationStripAccents(lower(right)))
			}
		}
		ctx := idSet(r.filter(ids, "country", "region", "macro"))
		if len(ctx) == 0 {
			continue
		}
		left := lower(strings.Join(words[:pos], " "))
		targets := r.lookup(left)
		if len(targets) == 0 {
			targets = r.lookup(locationStripAccents(left))
		}
		if len(targets) == 0 {
			if depth == 0 && pos >= 2 {
				nested := r.compound(words[:pos], 1)
				if nested != 0 && r.descendant(nested, ctx) {
					return nested
				}
			}
			continue
		}
		inside := r.within(targets, ctx)
		if len(inside) > 0 {
			return r.best(inside)
		}
		return r.best(targets)
	}
	return 0
}
func alphaToken(s string) bool {
	for _, c := range s {
		if !unicode.IsLetter(c) {
			return false
		}
	}
	return s != ""
}
func (r *LocationResolver) geo(text string) []int64 {
	if m := r.regex("_STATE_PREFIX_RE", text); m != nil {
		prefix := m.GroupByNumber(1).String()
		rest := locationTrim(m.GroupByNumber(2).String())
		city := locationTrim(strings.Split(rest, ",")[0])
		context := locationMap("_US_STATE_ABBREV", prefix)
		if context == "" {
			context = locationMap("_ISO2_TO_COUNTRY", prefix)
		}
		if context != "" {
			if ids := r.multi([]string{city, context}); len(ids) > 0 {
				return ids
			}
			if id := r.exact(city); id != 0 {
				return idList(id)
			}
		}
	}
	if m := r.regex("_COUNTRY_SUFFIX_RE", text); m != nil {
		city := locationTrim(m.GroupByNumber(1).String())
		code := m.GroupByNumber(2).String()
		country := locationMap("_COUNTRY_SUFFIX_ALIASES", code)
		if country == "" {
			country = locationMap("_ISO2_TO_COUNTRY", code)
		}
		if country == "" {
			country = locationMap("_ISO3_TO_COUNTRY", code)
		}
		if city != "" && country != "" {
			if ids := r.multi([]string{city, country}); len(ids) > 0 {
				return ids
			}
		}
	}
	n := utf8.RuneCountInString(text)
	if (n == 2 || n == 3) && alphaToken(text) {
		name := "_ISO2_TO_COUNTRY"
		if n == 3 {
			name = "_ISO3_TO_COUNTRY"
		}
		if country := locationMap(name, strings.ToUpper(text)); country != "" {
			if id := r.exact(country); id != 0 {
				return idList(id)
			}
		}
	}
	if id := r.exact(text); id != 0 {
		return idList(id)
	}
	// regexp2 Split includes captured groups; this expression has none.
	tokens, err := locationPatterns["_SPLIT_RE"].Split(text, -1)
	if err != nil {
		r.err = err
		return []int64{}
	}
	clean := []string{}
	for _, token := range tokens {
		if t := locationTrim(token); t != "" {
			clean = append(clean, t)
		}
	}
	if len(clean) == 0 {
		return []int64{}
	}
	if len(clean) == 1 {
		if id := r.single(clean[0]); id != 0 {
			return idList(id)
		}
		if strings.Contains(clean[0], " ") {
			words := strings.FieldsFunc(clean[0], space)
			if len(words) >= 2 {
				return idList(r.compound(words, 0))
			}
		}
		return []int64{}
	}
	return r.multi(clean)
}
func (r *LocationResolver) hint(raw string) string {
	if m := r.regex("_PAREN_HINT_RE", raw); m != nil {
		if t := normalizeLocationType(m.GroupByNumber(1).String()); t != "" {
			return t
		}
	}
	if m := r.regex("_TYPE_HINT_RE", raw); m != nil {
		return normalizeLocationType(m.String())
	}
	return ""
}
func (r *LocationResolver) one(raw, fallback string) []LocationResult {
	empty := []LocationResult{}
	raw = strings.Join(strings.FieldsFunc(raw, space), " ")
	for _, pattern := range []string{"_OTHER_LOCATIONS_RE", "_POSTAL_CODE_RE", "_OFFICE_SUFFIX_RE"} {
		raw = locationTrim(r.replace(pattern, raw))
	}
	if raw == "" || r.regex("_SKIP_RE", raw) != nil {
		return empty
	}
	finalType := normalizeLocationType(fallback)
	if finalType == "" {
		finalType = "onsite"
	}
	if m := r.regex("_COUNTRY_LOCATIONS_RE", raw); m != nil {
		ids := r.geo(locationTrim(m.GroupByNumber(1).String()))
		if len(ids) > 0 {
			return []LocationResult{{ptrID(ids[0]), finalType}}
		}
		return empty
	}
	if r.regex("_PURE_REMOTE_RE", raw) != nil {
		return []LocationResult{{nil, "remote"}}
	}
	if r.regex("_STANDALONE_TYPE_RE", raw) != nil {
		if t := normalizeLocationType(raw); t != "" {
			return []LocationResult{{nil, t}}
		}
		return empty
	}
	hint := r.hint(raw)
	geo := locationTrim(r.replace("_PAREN_HINT_RE", raw))
	geo = locationTrim(r.replace("_TYPE_HINT_RE", geo))
	geo = strings.Trim(geo, " -–/,")
	if geo == "" {
		if hint != "" {
			return []LocationResult{{nil, hint}}
		}
		return empty
	}
	if hint != "" {
		finalType = hint
	}
	ids := r.geo(geo)
	if len(ids) > 0 {
		for _, id := range ids {
			empty = append(empty, LocationResult{ptrID(id), finalType})
		}
		return empty
	}
	if r.tracking {
		r.locationMisses = append(r.locationMisses, LocationMiss{locationTrim(lower(geo)), geo})
	}
	return empty
}
func (r *LocationResolver) Resolve(raw []string, fallback, language string, tracking bool, negative []string) ([]LocationResult, []string, []LocationMiss, error) {
	r.reset()
	r.language = language
	r.tracking = tracking
	if negative != nil {
		r.negative = map[string]bool{}
		for _, key := range negative {
			r.negative[key] = true
		}
	}
	out := []LocationResult{}
	seen := map[string]bool{}
	for _, value := range raw {
		if !r.checkDeadline() {
			break
		}
		for _, segment := range strings.Split(value, ";") {
			segment = locationTrim(segment)
			if segment == "" {
				continue
			}
			for _, resolved := range r.one(segment, fallback) {
				key := fmt.Sprint(resolved.IDValue(), ":", resolved.Type)
				if !seen[key] {
					seen[key] = true
					out = append(out, resolved)
				}
			}
		}
	}
	keys := []string{}
	for key := range r.misses {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	if r.err != nil {
		return nil, nil, nil, r.err
	}
	return out, keys, r.locationMisses, nil
}
func (v LocationResult) IDValue() int64 {
	if v.ID == nil {
		return 0
	}
	return *v.ID
}
func (r *LocationResolver) Ancestors(id int64) ([]int64, error) {
	r.reset()
	out := []int64{id}
	current := id
	for hops := 0; hops < 20; hops++ {
		e := r.entry(current)
		if e == nil || e.parent == nil {
			break
		}
		out = appendUnique(out, *e.parent)
		current = *e.parent
	}
	return out, r.err
}
func (r *LocationResolver) DisplayName(id int64) (*string, error) {
	var name string
	err := r.db.QueryRow("SELECT name FROM display_name WHERE location_id=?", id).Scan(&name)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &name, nil
}

func (r *LocationResolver) checkDeadline() bool {
	if r.err != nil {
		return false
	}
	if time.Now().After(r.deadline) {
		r.err = fmt.Errorf("location operation deadline exceeded")
		return false
	}
	return true
}
