package apisniffer

import (
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

var unifrDeadlinePatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\bby\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+([0-3]?[0-9])(?:st|nd|rd|th)?(?:,\s*(20[0-9]{2}))?`),
	regexp.MustCompile(`(?i)\bbefore\s+([0-3]?[0-9])(?:st|nd|rd|th)?\s+of\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20[0-9]{2})`),
	regexp.MustCompile(`(?i)\bbewerbungsfrist:\s*([0-3]?[0-9])\.\s*(januar|februar|märz|april|mai|juni|juli|august|september|oktober|november|dezember)\s+(20[0-9]{2})`),
}
var unifrYears = regexp.MustCompile(`\b20[0-9]{2}\b`)
var unifrMonths = map[string]int{"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12, "januar": 1, "februar": 2, "märz": 3, "mai": 5, "juni": 6, "juli": 7, "oktober": 10, "dezember": 12}

func unifrAdjacentWord(text string, offset int, before bool) bool {
	var r rune
	if before {
		if offset == 0 {
			return false
		}
		r, _ = utf8.DecodeLastRuneInString(text[:offset])
	} else {
		if offset == len(text) {
			return false
		}
		r, _ = utf8.DecodeRuneInString(text[offset:])
	}
	return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r)
}

func UnifrDeadline(text string) (string, error) {
	text = inlineNormalized(text)
	unique := map[string]bool{}
	for index, pattern := range unifrDeadlinePatterns {
		for _, positions := range pattern.FindAllStringSubmatchIndex(text, -1) {
			// Python's word boundaries include Unicode letters and numbers.
			if unifrAdjacentWord(text, positions[0], true) {
				continue
			}
			match := make([]string, len(positions)/2)
			for i := range match {
				if positions[i*2] >= 0 {
					match[i] = text[positions[i*2]:positions[i*2+1]]
				}
			}
			day, month, year := match[2], strings.ToLower(match[1]), match[3]
			if index > 0 {
				day, month = match[1], strings.ToLower(match[2])
			}
			if year == "" {
				years := map[string]bool{}
				for _, p := range unifrYears.FindAllStringIndex(text, -1) {
					if !unifrAdjacentWord(text, p[0], true) && !unifrAdjacentWord(text, p[1], false) {
						years[text[p[0]:p[1]]] = true
					}
				}
				if len(years) != 1 {
					return "", ErrInventory
				}
				for y := range years {
					year = y
				}
			}
			d, err := strconv.Atoi(day)
			if err != nil {
				return "", ErrInventory
			}
			y, err := strconv.Atoi(year)
			if err != nil || unifrMonths[month] == 0 {
				return "", ErrInventory
			}
			value := time.Date(y, time.Month(unifrMonths[month]), d, 0, 0, 0, 0, time.UTC)
			if value.Year() != y || int(value.Month()) != unifrMonths[month] || value.Day() != d {
				return "", ErrInventory
			}
			unique[value.Format("2006-01-02")] = true
		}
	}
	if len(unique) > 1 {
		return "", ErrInventory
	}
	for value := range unique {
		return value, nil
	}
	return "", nil
}
