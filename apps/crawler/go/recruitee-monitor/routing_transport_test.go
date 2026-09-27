package recruitee

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type routeDoer func(*http.Request) (*http.Response, error)

func (f routeDoer) Do(r *http.Request) (*http.Response, error) { return f(r) }
func TestConfiguredEndpointRedirectsAndAccounting(t *testing.T) {
	endpoints := []string{}
	client := routeDoer(func(r *http.Request) (*http.Response, error) {
		endpoints = append(endpoints, r.URL.String())
		h := http.Header{}
		code := 200
		body := `{"offers":[]}`
		if len(endpoints) == 1 {
			h.Set("Location", "https://custom.example/final")
			code = 302
			body = "move"
		}
		return &http.Response{StatusCode: code, Header: h, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
	})
	result, err := fetchEndpoint(context.Background(), client, "https://jobs.example/api/offers")
	if err != nil || result.Requests != 2 || result.Responses != 2 || result.FinalURL != "https://custom.example/final" || result.Bytes != 4+len(`{"offers":[]}`) {
		t.Fatalf("%#v %v", result, err)
	}
	if endpoints[0] != "https://jobs.example/api/offers" {
		t.Fatal(endpoints)
	}
}
func TestRedirectTDMAndTransportFailure(t *testing.T) {
	for _, mode := range []string{"tdm", "transport", "loop", "insecure"} {
		t.Run(mode, func(t *testing.T) {
			count := 0
			client := routeDoer(func(r *http.Request) (*http.Response, error) {
				count++
				h := http.Header{}
				code := 302
				if count == 1 {
					h.Set("Location", "/next")
					if mode == "insecure" {
						h.Set("Location", "http://custom.example/next")
					}
				} else {
					switch mode {
					case "tdm":
						h.Set("TDM-Reservation", "1")
						h.Set("TDM-Policy", "https://policy.example")
						code = 200
					case "transport":
						return nil, errors.New("transport failed")
					default:
						h.Set("Location", "/next")
					}
				}
				return &http.Response{StatusCode: code, Header: h, Body: io.NopCloser(strings.NewReader("")), Request: r}, nil
			})
			result, err := Fetch(context.Background(), client, "acme")
			if err == nil {
				t.Fatal("accepted failed redirect")
			}
			switch mode {
			case "tdm":
				if result.ErrorKind != "tdm" || result.Requests != 2 || result.Bytes != 0 {
					t.Fatal(result)
				}
			case "transport":
				if result.Requests != 2 || result.Responses != 1 {
					t.Fatal(result)
				}
			case "loop":
				if result.Requests != 21 || result.Responses != 21 {
					t.Fatal(result)
				}
			case "insecure":
				if result.Requests != 1 {
					t.Fatal(result)
				}
			}
		})
	}
}
func TestFeedAboveFormer16MiBBound(t *testing.T) {
	body := strings.Repeat(" ", 17<<20) + `{"offers":[]}`
	client := &fakeClient{status: 200, body: body, header: http.Header{}}
	result, err := Fetch(context.Background(), client, "acme")
	if err != nil || result.Bytes != len(body) {
		t.Fatalf("%#v %v", result, err)
	}
}

type blockingBody struct{ closed chan struct{} }

func (b *blockingBody) Read(p []byte) (int, error) { <-b.closed; return 0, io.ErrClosedPipe }
func (b *blockingBody) Close() error               { close(b.closed); return nil }
func TestReadInactivityClosesBody(t *testing.T) {
	body := &blockingBody{closed: make(chan struct{})}
	reader := &readIdleBody{ReadCloser: body, timeout: 10 * time.Millisecond}
	_, err := reader.Read(make([]byte, 1))
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
}
