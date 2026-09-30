// Package resourcepolicy validates the narrow, credential-free policy signal
// shape. It never fetches a policy URL or interprets publisher permissions.
package resourcepolicy

import (
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"strings"
	"unicode/utf8"
)

const HeaderLimit = 8192

func Valid(signals *runtimev1.ResourcePolicySignals) bool {
	if signals == nil {
		return true
	}
	if len(signals.ProtoReflect().GetUnknown()) != 0 {
		return false
	}
	for _, value := range []*string{signals.TdmReservationHeader, signals.TdmPolicyHeader} {
		if value != nil && (len(*value) > HeaderLimit || !utf8.ValidString(*value) || strings.ContainsRune(*value, 0)) {
			return false
		}
	}
	return true
}
