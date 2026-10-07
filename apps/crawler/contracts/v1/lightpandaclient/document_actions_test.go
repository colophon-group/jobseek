package lightpandaclient

import (
	"context"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"strings"
	"testing"
	"time"
)

func TestDocumentActionsPinnedReservationBindingEOFAndCancellation(t *testing.T) {
	for _, mode := range []string{"document_ok", "document_binding", "document_trailing", "document_cancel"} {
		t.Run(mode, func(t *testing.T) {
			client, done := fixture(t, mode)
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			held, err := client.Reserve(ctx)
			if err != nil {
				t.Fatal(err)
			}
			request := actions.Request{Protocol: actions.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), Input: []byte{1}, Actions: []actions.Action{{Kind: "wait", TimeoutMS: 10000}}}
			if mode == "document_cancel" {
				time.AfterFunc(20*time.Millisecond, cancel)
			}
			response, err := held.DocumentActions(ctx, request)
			if mode == "document_ok" {
				if err != nil || !response.Valid() {
					t.Fatal("bound response rejected", err)
				}
			} else if err == nil || len(response.Result) != 0 {
				t.Fatal("misbound, trailing or cancelled response published")
			}
			<-done
			if _, err := held.DocumentActions(ctx, request); err == nil {
				t.Fatal("reservation reused")
			}
		})
	}
}
