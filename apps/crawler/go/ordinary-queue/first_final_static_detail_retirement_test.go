package queue

import "testing"

func TestRealFinalStaticDetailsColdRetirement(t *testing.T) {
	for _, provider := range []string{"paycor", "recruiterbox", "veryeast"} {
		t.Run(provider, func(t *testing.T) { testFirstAPIDetailRetirement(t, provider) })
	}
}
