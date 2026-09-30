package main

import (
	"github.com/chromedp/cdproto/network"
	"strings"
	"testing"
)

func TestMainDocumentPolicySignalsCarryOnlyBoundedPolicyHeaders(t *testing.T) {
	signals, invalid := mainDocumentPolicySignals(network.Headers{"TDM-Reservation": "1", "tdm-policy": "license", "Authorization": "ignored", "Set-Cookie": "ignored"})
	if invalid || signals.GetTdmReservationHeader() != "1" || signals.GetTdmPolicyHeader() != "license" {
		t.Fatal("main-document signals lost")
	}
	empty, invalid := mainDocumentPolicySignals(nil)
	if invalid || empty == nil || empty.TdmReservationHeader != nil || empty.TdmPolicyHeader != nil {
		t.Fatal("header-free evidence missing")
	}
	for _, headers := range []network.Headers{
		{"tdm-reservation": "1", "TDM-Reservation": "0"},
		{"tdm-reservation": 1},
		{"tdm-policy": strings.Repeat("x", 8193)},
		{"tdm-policy": "\x00"},
	} {
		if _, invalid := mainDocumentPolicySignals(headers); !invalid {
			t.Fatal("untrusted signal shape accepted")
		}
	}
}
