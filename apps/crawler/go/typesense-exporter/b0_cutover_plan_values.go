package main

import (
	"crypto/sha256"
	"encoding/hex"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
)

var b0PlanDecimal = regexp.MustCompile(`^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$`)

// PostgreSQL timestamps have microsecond precision. Keep the original decimal
// seconds in the plan; only the authenticated producer request rounds to ms.
func b0PlanSeconds(value time.Time) (string, error) {
	if value.Year() < 1 || value.Year() > 9999 || value.Nanosecond()%1000 != 0 {
		return "", errB0Cutover
	}
	micros := value.Unix()*1000000 + int64(value.Nanosecond()/1000)
	negative := micros < 0
	if negative {
		micros = -micros
	}
	result := strconv.FormatInt(micros/1000000, 10)
	if rem := micros % 1000000; rem != 0 {
		result += "." + strings.TrimRight(strconv.FormatInt(rem+1000000, 10)[1:], "0")
	}
	if negative {
		result = "-" + result
	}
	return result, nil
}

func b0PlanScore(value string) (*big.Rat, error) {
	if !b0PlanDecimal.MatchString(value) {
		return nil, errB0Cutover
	}
	score, ok := new(big.Rat).SetString(value)
	if !ok || score.Sign() < 0 || score.Cmp(big.NewRat(9999999999999, 1000)) > 0 {
		return nil, errB0Cutover
	}
	return score, nil
}

func b0PlanBoundGuard(raw string, identity b0CutoverIdentity, board, domain string) (string, string, error) {
	parts := strings.Split(raw, "|")
	if len(parts) != 7 || parts[0] != identity.Namespace || parts[1] != identity.Shard || parts[2] != strconv.FormatInt(identity.Epoch, 10) || parts[3] != board || parts[4] != domain {
		return "", "", errB0Cutover
	}
	kind := parts[5]
	if kind != "ft_browser" && kind != "ft_simple" && kind != "recurring_browser" && kind != "recurring_simple" {
		return "", "", errB0Cutover
	}
	if _, err := b0PlanScore(parts[6]); err != nil {
		return "", "", err
	}
	return kind, parts[6], nil
}

func b0PlanReadyAt(value time.Time, firstTime bool, sourceScore string) (string, error) {
	scheduled, err := b0PlanSeconds(value)
	if err != nil {
		return "", err
	}
	if firstTime {
		scheduled = "0"
	}
	if sourceScore == "" {
		return scheduled, nil
	}
	source, err := b0PlanScore(sourceScore)
	if err != nil {
		return "", err
	}
	if firstTime {
		return sourceScore, nil
	}
	current, ok := new(big.Rat).SetString(scheduled)
	if !ok {
		return "", errB0Cutover
	}
	if source.Cmp(current) > 0 {
		return sourceScore, nil
	}
	return scheduled, nil
}

func b0PlanCanonical(document map[string]any) ([]byte, string, error) {
	encoded, err := b0task.CanonicalJSON(document, true)
	if err != nil {
		return nil, "", errB0Cutover
	}
	digest := sha256.Sum256(encoded)
	return encoded, hex.EncodeToString(digest[:]), nil
}
