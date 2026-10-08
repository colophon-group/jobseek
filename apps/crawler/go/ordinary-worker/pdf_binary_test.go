package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"reflect"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestNativePDFBinaryExtractionOCRAndBoundsMatchPython(t *testing.T) {
	for _, name := range []string{"pdftotext", "pdfinfo", "pdftoppm", "tesseract"} {
		if _, err := exec.LookPath(name); err != nil {
			t.Fatal("required native PDF dependency absent", name)
		}
	}
	var cases []struct {
		Name, Body, Source string
		Config             map[string]any
		Output             map[string]any
		Error              bool
	}
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_pdf_binary.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 7 {
		t.Fatal("actual binary PDF reference missing", err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			body, err := base64.StdEncoding.DecodeString(c.Body)
			if err != nil {
				t.Fatal(err)
			}
			o, err := api.PDFOptionsFromConfig(c.Config)
			if err != nil {
				t.Fatal(err)
			}
			out, err := extractPDFBinary(context.Background(), body, c.Source, o)
			if (err != nil) != c.Error {
				t.Fatal(err, c.Error, out)
			}
			if c.Error {
				return
			}
			for key, want := range c.Output {
				if want != nil && !reflect.DeepEqual(out[key], want) {
					t.Fatal(key, out[key], want)
				}
			}
			for key, value := range out {
				if !reflect.DeepEqual(value, c.Output[key]) {
					t.Fatal(key, value, c.Output[key])
				}
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	o, _ := api.PDFOptionsFromConfig(nil)
	if _, err := extractPDFBinary(ctx, []byte("%PDF bad"), "https://example.com/file.pdf", o); !errors.Is(err, context.Canceled) {
		t.Fatal("native extraction ignored cancellation", err)
	}
	if _, err := extractPDFBinary(context.Background(), []byte("HTML error"), "https://example.com/file.pdf", o); err == nil {
		t.Fatal("non-PDF body accepted")
	}
}
