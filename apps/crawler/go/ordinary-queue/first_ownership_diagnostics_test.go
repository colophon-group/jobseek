package queue

import (
	"strings"
	"testing"
)

func TestFirstOwnershipProjectionDiagnosticRejectsUntrustedErrorContents(t *testing.T) {
	for _, message := range []string{
		"unrecognized Redis failure",
		"first ordinary retirement rejected: unknown",
		"first ordinary retirement rejected: cache\nprivate payload",
		"first ordinary retirement rejected: " + strings.Repeat("x", 128),
	} {
		if firstOwnershipProjectionReason(message) != "redis-refused" {
			t.Fatal("projection diagnostic exposed an untrusted Redis error")
		}
	}
}
