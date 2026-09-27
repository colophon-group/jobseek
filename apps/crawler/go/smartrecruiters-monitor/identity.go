package smartrecruiters

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"sync"
	"unicode"

	"golang.org/x/text/cases"
)

const brandField = "642d47ae571c9c5746eeeec4"
const departmentField = "642d47ae571c9c5746eeeec5"

var coordinateRE = regexp.MustCompile(`^-?(?:0|[1-9]\p{Nd}{0,2})(?:\.\p{Nd}{1,16})?$`)

func asciiDigits(s string) string {
	var b strings.Builder
	for _, r := range s {
		if r < 128 {
			b.WriteRune(r)
			continue
		}
		replaced := false
		for _, rr := range unicode.Nd.R16 {
			if uint32(r) >= uint32(rr.Lo) && uint32(r) <= uint32(rr.Hi) && (uint32(r)-uint32(rr.Lo))%uint32(rr.Stride) == 0 {
				b.WriteByte('0' + byte((uint32(r)-uint32(rr.Lo))/uint32(rr.Stride)%10))
				replaced = true
				break
			}
		}
		if !replaced {
			for _, rr := range unicode.Nd.R32 {
				if uint32(r) >= rr.Lo && uint32(r) <= rr.Hi && (uint32(r)-rr.Lo)%rr.Stride == 0 {
					b.WriteByte('0' + byte((uint32(r)-rr.Lo)/rr.Stride%10))
					replaced = true
					break
				}
			}
		}
		if !replaced {
			b.WriteRune(r)
		}
	}
	return b.String()
}
func NormalizeCoordinate(value any, latitude bool) (string, error) {
	raw, ok := value.(string)
	if !ok || !coordinateRE.MatchString(raw) {
		return "", errors.New("invalid bounded coordinate")
	}
	raw = asciiDigits(raw)
	n, ok := new(big.Rat).SetString(raw)
	if !ok {
		return "", errors.New("invalid coordinate")
	}
	bound := int64(180)
	if latitude {
		bound = 90
	}
	if n.Cmp(big.NewRat(-bound, 1)) < 0 || n.Cmp(big.NewRat(bound, 1)) > 0 {
		return "", errors.New("coordinate out of range")
	}
	if n.Sign() == 0 {
		return "0", nil
	}
	if strings.Contains(raw, ".") {
		raw = strings.TrimRight(strings.TrimRight(raw, "0"), ".")
	}
	return raw, nil
}
func jsonASCII(value any) (string, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(value); err != nil {
		return "", err
	}
	var result strings.Builder
	for _, r := range strings.TrimSuffix(b.String(), "\n") {
		if r < 128 {
			result.WriteRune(r)
		} else if r <= 0xffff {
			fmt.Fprintf(&result, `\u%04x`, r)
		} else {
			r -= 0x10000
			fmt.Fprintf(&result, `\u%04x\u%04x`, 0xd800+(r>>10), 0xdc00+(r&0x3ff))
		}
	}
	return result.String(), nil
}
func customValue(detail Object, id string) (string, error) {
	fields, _ := detail["customField"].([]any)
	seen := map[string]bool{}
	for _, raw := range fields {
		field := object(raw)
		if field["fieldId"] != id {
			continue
		}
		s, ok := field["valueId"].(string)
		if ok && length(s) > 0 && length(s) <= 128 && !strings.ContainsRune(s, 0) {
			seen[s] = true
		}
	}
	if len(seen) > 1 {
		return "", errors.New("conflicting custom field IDs")
	}
	for value := range seen {
		return value, nil
	}
	return "", nil
}
func locationIdentity(detail Object) (string, string, error) {
	loc, err := requireObject(detail["location"], "location")
	if err != nil {
		return "", "", err
	}
	lat, lon := loc["latitude"], loc["longitude"]
	if (lat == nil) != (lon == nil) {
		return "", "", errors.New("both coordinates required")
	}
	if lat != nil {
		a, err := NormalizeCoordinate(lat, true)
		if err != nil {
			return "", "", err
		}
		b, err := NormalizeCoordinate(lon, false)
		return "geo", a + "," + b, err
	}
	country, ok := loc["country"].(string)
	if !ok || !languageRE.MatchString(strings.ToLower(country)) {
		return "", "", errors.New("country code required")
	}
	postal, ok := loc["postalCode"].(string)
	if !ok || trim(postal) == "" || length(postal) > 32 || strings.ContainsRune(postal, 0) {
		return "", "", errors.New("postal code required")
	}
	brand, err := customValue(detail, brandField)
	if err != nil {
		return "", "", err
	}
	department, err := customValue(detail, departmentField)
	if err != nil {
		return "", "", err
	}
	dept := object(detail["department"])["id"]
	if brand == "" || department == "" || dept == nil || pyString(dept) == "" {
		return "", "", errors.New("provider department/brand IDs required")
	}
	payload := Object{"brand": brand, "country": strings.ToLower(country), "department": pyString(dept), "department_value": department, "hybrid": loc["hybrid"] == true, "postal_code": cases.Fold().String(trim(postal)), "remote": loc["remote"] == true}
	value, err := jsonASCII(payload)
	return "provider-codes", value, err
}

type identity struct{ JobID, Kind, Value string }

func identityComponents(detail Object) (identity, error) {
	id, ok := detail["jobId"].(string)
	if !ok || !uuidRE.MatchString(id) {
		return identity{}, errors.New("UUID jobId required")
	}
	kind, value, err := locationIdentity(detail)
	return identity{strings.ToLower(id), kind, value}, err
}
func sourceIdentity(token string, id identity) (string, error) {
	body, err := jsonASCII(Object{"location_kind": id.Kind, "location_value": id.Value, "provider": "smartrecruiters", "tenant": token, "version": 1, "job_id": id.JobID})
	if err != nil {
		return "", err
	}
	value := fmt.Sprintf("smartrecruiters:%s:%s/%s/%x", strings.ToLower(token), id.JobID, id.Kind, sha256.Sum256([]byte(body)))
	if !sourceIdentityRE.MatchString(value) {
		return "", errors.New("invalid bounded source identity")
	}
	return value, nil
}
func language(detail Object, localized bool) string {
	raw := detail["language"]
	if m := object(raw); m != nil {
		raw = m["code"]
	} else if !localized {
		return ""
	}
	code, ok := raw.(string)
	if !ok {
		return ""
	}
	if localized {
		match := localizedLanguageRE.FindStringSubmatch(trim(code))
		if len(match) > 1 {
			return strings.ToLower(match[1])
		}
		return ""
	}
	code = cases.Fold().String(code)
	if !languageRE.MatchString(code) {
		return ""
	}
	return code
}
func validateDetail(token, id string, detail Object) error {
	if pyString(detail["id"]) != id || object(detail["company"])["identifier"] != token {
		return errors.New("detail publication/tenant mismatch")
	}
	if detail["active"] == false {
		return ErrInactiveDetail
	}
	if detail["active"] != true {
		return errors.New("detail not affirmatively active")
	}
	job, ok := detail["jobId"].(string)
	if !ok || !jobIDRE.MatchString(job) {
		return errors.New("invalid UUID jobId")
	}
	return nil
}
func stableFields(value any) (map[string]string, error) {
	fields, ok := value.([]any)
	if !ok || len(fields) == 0 {
		return nil, errors.New("nonempty customField list required")
	}
	result := map[string]string{}
	for _, value := range fields {
		field, ok := value.(map[string]any)
		if !ok {
			return nil, errors.New("invalid custom field")
		}
		id, ok1 := field["fieldId"].(string)
		v, ok2 := field["valueId"].(string)
		if !ok1 || !ok2 || length(id) < 1 || length(v) < 1 || length(id) > 128 || length(v) > 128 {
			return nil, errors.New("bounded custom field IDs required")
		}
		if _, exists := result[id]; exists {
			return nil, errors.New("duplicate custom field ID")
		}
		result[id] = v
	}
	return result, nil
}
func validateLocationOverlap(listed, detail Object) error {
	if listed == nil || detail == nil {
		return errors.New("location mappings required")
	}
	for _, key := range []string{"country", "postalCode", "remote", "hybrid"} {
		a, ap := listed[key]
		b, bp := detail[key]
		if ap != bp || !reflect.DeepEqual(a, b) {
			return errors.New("location field drift")
		}
	}
	country, ok := listed["country"].(string)
	if !ok || !languageRE.MatchString(strings.ToLower(country)) {
		return errors.New("invalid listed country")
	}
	if postal := listed["postalCode"]; postal != nil {
		s, ok := postal.(string)
		if !ok || trim(s) == "" || length(s) > 32 || strings.ContainsRune(s, 0) {
			return errors.New("invalid listed postal code")
		}
	}
	for _, key := range []string{"remote", "hybrid"} {
		if _, ok := listed[key].(bool); !ok {
			return errors.New("location flags must be boolean")
		}
	}
	_, la := listed["latitude"]
	_, lo := listed["longitude"]
	_, da := detail["latitude"]
	_, do := detail["longitude"]
	if (la || lo) != (da || do) {
		return errors.New("coordinate presence drift")
	}
	if la || lo {
		for _, key := range []string{"latitude", "longitude"} {
			a, err := NormalizeCoordinate(listed[key], key == "latitude")
			if err != nil {
				return err
			}
			b, err := NormalizeCoordinate(detail[key], key == "latitude")
			if err != nil {
				return err
			}
			if a != b {
				return errors.New("coordinate drift")
			}
		}
	}
	return nil
}
func validateCanonicalDetail(token string, listed, detail Object) error {
	if !reflect.DeepEqual(listed["id"], detail["id"]) || object(detail["company"])["identifier"] != token || detail["active"] != true || detail["visibility"] != "PUBLIC" {
		return errors.New("canonical detail identity mismatch")
	}
	for _, key := range []string{"uuid", "jobAdId", "defaultJobAd", "refNumber", "releasedDate", "visibility"} {
		a, ap := listed[key]
		b, bp := detail[key]
		if !ap || !bp || !reflect.DeepEqual(a, b) {
			return fmt.Errorf("list/detail drift at %s", key)
		}
	}
	for _, key := range []string{"uuid", "jobAdId"} {
		s, ok := listed[key].(string)
		if !ok || !uuidRE.MatchString(s) {
			return errors.New("UUID overlap required")
		}
	}
	if _, ok := listed["defaultJobAd"].(bool); !ok {
		return errors.New("boolean defaultJobAd required")
	}
	for _, key := range []string{"refNumber", "releasedDate"} {
		s, ok := listed[key].(string)
		if !ok || length(s) < 1 || length(s) > 128 {
			return errors.New("bounded overlap text required")
		}
	}
	if listed["ref"] != DetailURL(token, pyString(listed["id"])) || object(listed["company"])["identifier"] != token {
		return errors.New("listed endpoint/tenant mismatch")
	}
	lang := language(listed, false)
	if lang == "" || lang != language(detail, false) {
		return errors.New("language drift")
	}
	loc := object(listed["location"])
	if err := validateLocationOverlap(loc, object(detail["location"])); err != nil {
		return err
	}
	if loc["latitude"] == nil {
		a, b := object(listed["department"]), object(detail["department"])
		if len(a) == 0 || b == nil || a["id"] == nil || b["id"] == nil || pyString(a["id"]) != pyString(b["id"]) {
			return errors.New("department ID drift")
		}
		af, err := stableFields(listed["customField"])
		if err != nil {
			return err
		}
		bf, err := stableFields(detail["customField"])
		if err != nil {
			return err
		}
		for _, key := range []string{brandField, departmentField} {
			if af[key] == "" || af[key] != bf[key] {
				return errors.New("custom field identity drift")
			}
		}
	}
	return nil
}
func fetchDetails(ctx context.Context, opt Options, items []Object, get GetJSON) ([]Object, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	result := make([]Object, len(items))
	var wg sync.WaitGroup
	var once sync.Once
	var first error
	work := make(chan int)
	for worker := 0; worker < 12; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for index := range work {
				id, _ := PublicationID(items[index]["id"])
				detail, err := get(ctx, DetailURL(opt.Token, id), DetailResponseMaxBytes)
				if err == nil {
					if opt.Identity == "job-location-v1" {
						err = validateCanonicalDetail(opt.Token, items[index], detail)
					} else {
						err = validateDetail(opt.Token, id, detail)
					}
				}
				if err != nil {
					once.Do(func() { first = err; cancel() })
					return
				}
				result[index] = detail
			}
		}()
	}
send:
	for i := range items {
		select {
		case work <- i:
		case <-ctx.Done():
			break send
		}
	}
	close(work)
	wg.Wait()
	if first != nil {
		return nil, first
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return result, nil
}

func localization(job Job) Object {
	return Object{"title": job.Title, "description": job.Description, "locations": job.Locations}
}
func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}
func variantLess(a, b Object, opt Options, canonical bool) bool {
	if canonical {
		ad, bd := a["defaultJobAd"] == true, b["defaultJobAd"] == true
		if ad != bd {
			return ad
		}
	} else {
		rank := func(d Object) int {
			lang := language(d, true)
			for i, s := range opt.Languages {
				if s == lang {
					return i
				}
			}
			return len(opt.Languages)
		}
		ar, br := rank(a), rank(b)
		if ar != br {
			return ar < br
		}
	}
	al, bl := language(a, !canonical), language(b, !canonical)
	if al == "" {
		al = "zz"
	}
	if bl == "" {
		bl = "zz"
	}
	if al != bl {
		return al < bl
	}
	if !canonical {
		ad, bd := a["defaultJobAd"] == true, b["defaultJobAd"] == true
		if ad != bd {
			return ad
		}
	}
	return pyString(a["id"]) < pyString(b["id"])
}
func projectVariants(opt Options, id identity, variants []Object, canonical bool) (Job, error) {
	ordered := append([]Object(nil), variants...)
	sort.SliceStable(ordered, func(i, j int) bool { return variantLess(ordered[i], ordered[j], opt, canonical) })
	primary := ordered[0]
	job, err := ParseDetail(primary)
	if err != nil {
		return job, err
	}
	localizations := Object{}
	ids := []string{}
	defaults := 0
	for _, detail := range ordered {
		if detail["defaultJobAd"] == true {
			defaults++
		}
		lang := language(detail, !canonical)
		ids = append(ids, pyString(detail["id"]))
		if canonical && (lang == "" || localizations[lang] != nil) {
			return Job{}, errors.New("canonical language missing or duplicated")
		}
		if lang != "" && localizations[lang] == nil {
			content, err := ParseDetail(detail)
			if err != nil {
				return Job{}, err
			}
			if canonical && (!truth(content.Title) || !truth(content.Description) || !truth(content.Locations)) {
				return Job{}, errors.New("required rich content missing")
			}
			localizations[lang] = localization(content)
		}
	}
	if canonical && defaults > 1 {
		return Job{}, errors.New("multiple default publications")
	}
	job.URL = PostingURL(opt.Token, pyString(primary["id"]))
	job.Language = nullable(language(primary, !canonical))
	if len(localizations) > 0 {
		job.Localizations = localizations
	}
	if job.Metadata == nil {
		job.Metadata = Object{}
	}
	job.Metadata["smartrecruiters_job_id"] = id.JobID
	if canonical {
		identity, err := sourceIdentity(opt.Token, id)
		if err != nil {
			return Job{}, err
		}
		job.SourceIdentity = identity
	} else {
		sort.Strings(ids)
		job.Metadata["smartrecruiters_publication_id"] = pyString(primary["id"])
		job.Metadata["smartrecruiters_ref_number"] = primary["refNumber"]
		if opt.Template != nil {
			job.URL = strings.ReplaceAll(*opt.Template, "{job_id}", id.JobID)
		} else {
			value := "smartrecruiters:" + strings.ToLower(opt.Token) + ":" + id.JobID
			if !sourceIdentityRE.MatchString(value) {
				return Job{}, errors.New("invalid bounded source identity")
			}
			job.SourceIdentity = value
		}
	}
	job.Metadata["smartrecruiters_publication_ids"] = ids
	return job, nil
}

// ProjectDetails supports all currently configured identity modes, including
// coordinate-backed and provider-code fallback location identities.
func ProjectDetails(opt Options, details []Object) ([]Job, error) {
	jobs := []Job{}
	grouped := map[identity][]Object{}
	byJob := map[string][]identity{}
	canonical := opt.Identity == "job-location-v1"
	for _, detail := range details {
		var id identity
		if canonical {
			var err error
			id, err = identityComponents(detail)
			if err != nil {
				return nil, err
			}
		} else {
			id.JobID = strings.ToLower(pyString(detail["jobId"]))
		}
		grouped[id] = append(grouped[id], detail)
		byJob[id.JobID] = append(byJob[id.JobID], id)
	}
	if canonical {
		for _, records := range byJob {
			if len(records) > 1 {
				for _, id := range records {
					if id.Kind != "geo" {
						return nil, errors.New("repeated jobId requires provider coordinates")
					}
				}
			}
		}
	}
	identities := make([]identity, 0, len(grouped))
	for id := range grouped {
		identities = append(identities, id)
	}
	sort.Slice(identities, func(i, j int) bool {
		a, b := identities[i], identities[j]
		if a.JobID != b.JobID {
			return a.JobID < b.JobID
		}
		if a.Kind != b.Kind {
			return a.Kind < b.Kind
		}
		return a.Value < b.Value
	})
	urls := map[string]bool{}
	for _, id := range identities {
		job, err := projectVariants(opt, id, grouped[id], canonical)
		if err != nil {
			return nil, err
		}
		if canonical && urls[job.URL] {
			return nil, errors.New("canonical URL collision")
		}
		urls[job.URL] = true
		jobs = append(jobs, job)
	}
	if canonical {
		sort.Slice(jobs, func(i, j int) bool { return jobs[i].URL < jobs[j].URL })
	}
	return jobs, nil
}
