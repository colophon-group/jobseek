package queue

import "errors"

// Rejections preserve the fail-closed sentinel and retain only code-owned
// categories, never a claim, token, cache value or SQL message.
type authorityRejection uint8

const (
	epochRejection authorityRejection = iota + 1
	cacheSnapshotRejection
	leaseRejection
	fenceRejection
	receiptRejection
	acknowledgementRejection
)

func (authorityRejection) Error() string { return ErrAuthorityLost.Error() }
func (authorityRejection) Unwrap() error { return ErrAuthorityLost }

// AuthorityLossReason exposes a bounded diagnostic without granting recovery
// authority or changing errors.Is(err, ErrAuthorityLost).
func AuthorityLossReason(err error) string {
	var rejected authorityRejection
	if !errors.As(err, &rejected) {
		return ""
	}
	switch rejected {
	case epochRejection:
		return "routing_epoch"
	case cacheSnapshotRejection:
		return "cache_snapshot"
	case leaseRejection:
		return "lease"
	case fenceRejection:
		return "write_fence"
	case receiptRejection:
		return "receipt"
	case acknowledgementRejection:
		return "acknowledgement"
	default:
		return ""
	}
}
