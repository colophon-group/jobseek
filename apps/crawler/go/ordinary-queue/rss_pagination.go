package queue

import (
	"encoding/json"
	"regexp"
)

type RSSPagination struct {
	Param                                string
	Start, Increment, PageSize, MaxPages int
}

var rssPageParameter = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_.-]{0,63}$`)

// Parse existing generic/WordPress pagination without granting ownership.
// Fetch integration must preserve one stream across page boundaries.
func RSSPaginationOptions(metadata, preset string) (*RSSPagination, error) {
	md, err := profileMetadataFields(metadata, nil)
	if err != nil {
		return nil, err
	}
	raw, present := md["pagination"]
	if preset != "generic" {
		if present {
			return nil, ErrUnsupportedProfile
		}
		if preset == "wp_job_manager" {
			return &RSSPagination{Param: "paged", Start: 1, Increment: 1, PageSize: 10}, nil
		}
		return nil, nil
	}
	if !present || string(raw) == "null" {
		return nil, nil
	}
	f, err := profileMetadataFields(string(raw), map[string]bool{"param_name": true, "start": true, "increment": true, "page_size": true, "max_pages": true})
	if err != nil {
		return nil, err
	}
	p := &RSSPagination{Start: 1, Increment: 1}
	if json.Unmarshal(f["param_name"], &p.Param) != nil || !rssPageParameter.MatchString(p.Param) || f["page_size"] == nil || f["max_pages"] == nil {
		return nil, ErrUnsupportedProfile
	}
	for key, target := range map[string]*int{"start": &p.Start, "increment": &p.Increment, "page_size": &p.PageSize, "max_pages": &p.MaxPages} {
		if value, ok := f[key]; ok && (string(value) == "null" || json.Unmarshal(value, target) != nil) {
			return nil, ErrUnsupportedProfile
		}
		maximum := 10_000_000
		if key == "page_size" {
			maximum = 1000
		}
		if key == "max_pages" {
			maximum = 10000
		}
		if *target < 1 || *target > maximum {
			return nil, ErrUnsupportedProfile
		}
	}
	if int64(p.Start)+int64(p.MaxPages-1)*int64(p.Increment) > 10_000_000 {
		return nil, ErrUnsupportedProfile
	}
	return p, nil
}
