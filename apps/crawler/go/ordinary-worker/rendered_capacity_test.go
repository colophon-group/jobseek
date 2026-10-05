package worker

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"syscall"
	"testing"
	"time"

	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
)

func TestOrdinaryRenderedCapacityWaitResumesWithoutUpstreamFailure(t *testing.T) {
	calls := 0
	held := &lp.Reservation{}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	actual, err := waitRenderedReservation(ctx, func(context.Context) (*lp.Reservation, error) {
		calls++
		if calls == 1 {
			return nil, fmt.Errorf("renderer TLS handshake: %w", errors.Join(lp.ErrReservationUnavailable, io.EOF))
		}
		return held, nil
	})
	if err != nil || actual != held || calls != 2 {
		t.Fatalf("shared renderer slot release did not resume the owned claim: %v", err)
	}
}

func TestOrdinaryRenderedCapacityWaitHonorsClaimCancellation(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	calls := 0
	actual, err := waitRenderedReservation(ctx, func(context.Context) (*lp.Reservation, error) {
		calls++
		return nil, fmt.Errorf("renderer TLS handshake: %w", errors.Join(lp.ErrReservationUnavailable, &net.OpError{Op: "read", Net: "tcp", Err: &os.SyscallError{Syscall: "read", Err: syscall.ECONNRESET}}))
	})
	if actual != nil || !errors.Is(err, context.DeadlineExceeded) || calls != 1 {
		t.Fatalf("capacity wait ignored cancellation or spun: %v", err)
	}
}

func TestOrdinaryRenderedIdentityFailureIsNotCapacity(t *testing.T) {
	failure := errors.New("renderer negotiated invalid identity")
	calls := 0
	actual, err := waitRenderedReservation(context.Background(), func(context.Context) (*lp.Reservation, error) {
		calls++
		return nil, failure
	})
	if actual != nil || !errors.Is(err, failure) || calls != 1 {
		t.Fatalf("renderer identity failure was retried as capacity: %v", err)
	}
}

func TestUnclassifiedResetDoesNotEnterReservationWait(t *testing.T) {
	failure := fmt.Errorf("other transport phase: %w", syscall.ECONNRESET)
	calls := 0
	_, err := waitRenderedReservation(context.Background(), func(context.Context) (*lp.Reservation, error) { calls++; return nil, failure })
	if !errors.Is(err, failure) || calls != 1 {
		t.Fatalf("unclassified reset was retried: %v", err)
	}
}
