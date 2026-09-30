package publisherpolicy

import (
	"encoding/json"
	"errors"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestFrozenPythonResourceSignals(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name     string
		Headers  map[string]string
		Body     string
		Expected *struct {
			URL, Source string
			PolicyURL   *string `json:"policy_url"`
		}
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			signals := &runtimev1.ResourcePolicySignals{}
			if value, ok := c.Headers["tdm-reservation"]; ok {
				signals.TdmReservationHeader = &value
			}
			if value, ok := c.Headers["tdm-policy"]; ok {
				signals.TdmPolicyHeader = &value
			}
			err := Check(signals, c.Body, "https://publisher.invalid/job")
			var reservation *Reservation
			if c.Expected == nil {
				if err != nil {
					t.Fatal(err)
				}
				return
			}
			if !errors.As(err, &reservation) || reservation.URL != c.Expected.URL || reservation.Source != c.Expected.Source || !reflect.DeepEqual(reservation.PolicyURL, c.Expected.PolicyURL) {
				t.Fatalf("Python policy mismatch: %#v / %#v", reservation, c.Expected)
			}
		})
	}
}
func TestSignalBoundsAndUnknownFieldsFailClosed(t *testing.T) {
	for _, text := range []string{strings.Repeat("x", 8193), "\x00", string([]byte{0xff})} {
		if !errors.Is(Check(&runtimev1.ResourcePolicySignals{TdmPolicyHeader: &text}, "", "https://publisher.invalid/job"), ErrSignals) {
			t.Fatal("invalid signal accepted")
		}
	}
	signals := &runtimev1.ResourcePolicySignals{}
	signals.ProtoReflect().SetUnknown([]byte{0x80, 0x01, 0x01})
	if !errors.Is(Check(signals, "", "https://publisher.invalid/job"), ErrSignals) {
		t.Fatal("unknown policy accepted")
	}
}
