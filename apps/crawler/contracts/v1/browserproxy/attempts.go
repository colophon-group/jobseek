package browserproxy

import (
	"encoding/json"
	"math"
)

// OriginBlockAttempts matches the original browser retry helper's maximum of
// five operations. Admission retains unsupported/coerced config values.
func OriginBlockAttempts(raw any) (int, error) {
	if raw == nil {
		return 1, nil
	}
	var n float64
	switch value := raw.(type) {
	case float64:
		n = value
	case json.Number:
		var err error
		n, err = value.Float64()
		if err != nil {
			return 0, ErrPolicy
		}
	default:
		return 0, ErrPolicy
	}
	if math.IsNaN(n) || math.IsInf(n, 0) || n < 1 || n > 5 || n != math.Trunc(n) {
		return 0, ErrPolicy
	}
	return int(n), nil
}
