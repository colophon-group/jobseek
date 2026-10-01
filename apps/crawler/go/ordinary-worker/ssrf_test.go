package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/netip"
	"os"
	"testing"
)

func TestSSRFActualPythonAddressAndDNSOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_ssrf.json")
	if err != nil {
		t.Fatal(err)
	}
	var captured struct {
		Literals []struct {
			IP      string
			Blocked bool
		}
		DNS []struct {
			Name, First string
			Blocked     bool
			Addresses   []struct{ IP, Zone string }
		}
	}
	if err := json.Unmarshal(body, &captured); err != nil {
		t.Fatal(err)
	}
	if len(captured.Literals) < 200 || len(captured.DNS) != 9 {
		t.Fatal("missing actual Python policy boundaries")
	}
	for _, item := range captured.Literals {
		t.Run(item.IP, func(t *testing.T) {
			addr, err := netip.ParseAddr(item.IP)
			got := err != nil || blockedAddress(addr)
			if got != item.Blocked {
				t.Fatalf("Python address policy differs: blocked=%v want=%v", got, item.Blocked)
			}
		})
	}
	for _, item := range captured.DNS {
		t.Run(item.Name, func(t *testing.T) {
			var answers []netip.Addr
			for _, value := range item.Addresses {
				addr := netip.MustParseAddr(value.IP)
				if value.Zone != "" {
					addr = addr.WithZone(value.Zone)
				}
				answers = append(answers, addr)
			}
			got, err := validatedAddresses(answers)
			if (err != nil) != item.Blocked || (err == nil && got[0].String() != item.First) {
				t.Fatalf("Python all-answer DNS guard differs: %v %v", got, err)
			}
		})
	}
}

func TestSSRFDNSRetryOnlyTemporaryAndCancellation(t *testing.T) {
	for _, scenario := range []string{"temporary_success", "temporary_exhausted", "permanent", "timeout", "cancel", "private_after_temporary"} {
		t.Run(scenario, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			attempts := 0
			lookup := func(context.Context, string) ([]netip.Addr, error) {
				attempts++
				if scenario == "cancel" {
					cancel()
				}
				if attempts == 3 && scenario == "temporary_success" {
					return []netip.Addr{netip.MustParseAddr("8.8.8.8")}, nil
				}
				if attempts == 3 && scenario == "private_after_temporary" {
					return []netip.Addr{netip.MustParseAddr("8.8.8.8"), netip.MustParseAddr("127.0.0.1")}, nil
				}
				return nil, &net.DNSError{Err: "private resolver diagnostic", IsTemporary: scenario != "permanent", IsTimeout: scenario == "timeout"}
			}
			_, err := resolvePublic(ctx, "fixture.invalid", lookup)
			want := 1
			if scenario == "temporary_success" || scenario == "temporary_exhausted" || scenario == "private_after_temporary" {
				want = 3
			}
			if attempts != want || (err == nil) != (scenario == "temporary_success") || (scenario == "cancel" && !errors.Is(err, context.Canceled)) {
				t.Fatalf("DNS bounded retry/cancel mismatch: attempts=%d error=%v", attempts, err)
			}
		})
	}
}
