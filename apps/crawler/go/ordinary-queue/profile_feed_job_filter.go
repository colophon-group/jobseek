package queue

import (
	"encoding/json"
	"strings"
	"unicode/utf8"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"github.com/dlclark/regexp2/v2"
)

type FeedJobFilter struct {
	Field                 string
	include, exclude      *regexp2.Regexp
	requireClassification bool
}

func feedJobFilter(raw json.RawMessage) (*FeedJobFilter, error) {
	if raw == nil || string(raw) == "null" || string(raw) == "false" || string(raw) == `""` || string(raw) == "{}" {
		return nil, nil
	}
	f := &FeedJobFilter{}
	var include, exclude string
	if json.Unmarshal(raw, &include) != nil {
		options, err := profileMetadataFields(string(raw), map[string]bool{"include": true, "exclude": true, "field": true, "require_classification": true})
		if err != nil {
			return nil, err
		}
		for key, value := range options {
			if string(value) == "null" && key != "require_classification" {
				continue
			}
			switch key {
			case "include":
				if json.Unmarshal(value, &include) != nil {
					return nil, ErrUnsupportedProfile
				}
			case "exclude":
				if json.Unmarshal(value, &exclude) != nil {
					return nil, ErrUnsupportedProfile
				}
			case "field":
				if json.Unmarshal(value, &f.Field) != nil {
					return nil, ErrUnsupportedProfile
				}
			case "require_classification":
				if json.Unmarshal(value, &f.requireClassification) != nil || string(value) == "null" {
					return nil, ErrUnsupportedProfile
				}
			}
		}
	}
	if f.Field != "" && f.Field != "title" && f.Field != "description" && f.Field != "locations" && f.Field != "metadata" && !strings.HasPrefix(f.Field, "metadata.") || utf8.RuneCountInString(f.Field) > 256 || strings.ContainsRune(f.Field, 0) {
		return nil, ErrUnsupportedProfile
	}
	if f.requireClassification && (include == "" || exclude == "") {
		return nil, ErrUnsupportedProfile
	}
	var err error
	if include != "" {
		f.include, err = dom.CompileURLPattern(include)
		if err != nil {
			return nil, ErrUnsupportedProfile
		}
	}
	if exclude != "" {
		f.exclude, err = dom.CompileURLPattern(exclude)
		if err != nil {
			return nil, ErrUnsupportedProfile
		}
	}
	return f, nil
}

func (f *FeedJobFilter) Matches(text string) (bool, error) {
	if f == nil {
		return true, nil
	}
	include, exclude := false, false
	var err error
	if f.include != nil {
		include, err = f.include.MatchString(text)
		if err != nil {
			return false, err
		}
	}
	if f.exclude != nil {
		exclude, err = f.exclude.MatchString(text)
		if err != nil {
			return false, err
		}
	}
	if f.requireClassification && include == exclude {
		return false, ErrProviderClassification
	}
	return (f.include == nil || include) && !exclude, nil
}
