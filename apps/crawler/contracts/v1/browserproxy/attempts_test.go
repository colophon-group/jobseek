package browserproxy

import (
	"encoding/json"
	"math"
	"testing"
)

func TestOriginalBrowserOriginBlockAttemptBounds(t *testing.T) {
	for _, raw := range []any{nil, float64(1), float64(5), json.Number("3.0"), json.Number("5")} {
		attempts, err := OriginBlockAttempts(raw)
		if err != nil || attempts < 1 || attempts > 5 {
			t.Fatal("valid original retry bound rejected")
		}
	}
	for _, raw := range []any{true, "3", float64(0), float64(6), float64(2.5), math.Inf(1), math.NaN(), json.Number("broken")} {
		if _, err := OriginBlockAttempts(raw); err == nil {
			t.Fatal("unsupported or unbounded retry value admitted")
		}
	}
}
