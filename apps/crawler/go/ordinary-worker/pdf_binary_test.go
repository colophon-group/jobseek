package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestNativePDFConfiguredTitleSurvivesNativeTextProjection(t *testing.T) {
	dir := t.TempDir()
	script := "#!/bin/sh\nexec /bin/cat \"${0%/*}/text\"\n"
	if err := os.WriteFile(filepath.Join(dir, "go-pdf-text"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "text"), []byte("Title: Research Scientist\nLocation: Nyon\n"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
	o, err := api.PDFOptionsFromConfig(map[string]any{"title_source": "text", "title_pattern": `(?m)^Title: (.+)$`, "location_pattern": `(?m)^Location: (.+)$`})
	if err != nil {
		t.Fatal(err)
	}
	out, err := extractPDFBinary(context.Background(), []byte("%PDF test"), "https://example.com/posting.pdf", o)
	if err != nil || out["title"] != "Research Scientist" || !reflect.DeepEqual(out["locations"], []any{"Nyon"}) {
		t.Fatal(out, err)
	}
}

func TestNativePDFBinaryExtractionOCRAndBoundsMatchPython(t *testing.T) {
	for _, name := range []string{"go-pdf-text", "pdfinfo", "pdftoppm", "tesseract"} {
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

func TestNativePDFSubprocessCancellationAndOutputBounds(t *testing.T) {
	for _, mode := range []string{"cancel", "oversize"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			script := "#!/bin/sh\nwhile :; do :; done\n"
			if mode == "oversize" {
				script = "#!/bin/sh\nexec /usr/bin/head -c 16777217 /dev/zero\n"
			}
			if err := os.WriteFile(filepath.Join(dir, "go-pdf-text"), []byte(script), 0700); err != nil {
				t.Fatal(err)
			}
			t.Setenv("PATH", dir)
			ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
			if mode == "oversize" {
				cancel()
				ctx, cancel = context.WithTimeout(context.Background(), 5*time.Second)
			}
			defer cancel()
			start := time.Now()
			out, err := nativePDFText(ctx, []byte("%PDF fixture"))
			if err == nil || out != nil {
				t.Fatal("failed extraction returned partial text")
			}
			if mode == "cancel" && (!errors.Is(err, context.DeadlineExceeded) || time.Since(start) > 2*time.Second) {
				t.Fatal("child not reaped after cancellation", err)
			}
		})
	}
}
