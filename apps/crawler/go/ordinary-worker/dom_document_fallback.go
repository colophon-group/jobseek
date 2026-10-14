package worker

import (
	"bytes"
	"context"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func parseDOMBinaryDocument(ctx context.Context, body []byte, source string, formats map[string]any) (map[string]any, error) {
	if err := api.ValidateDOMDocumentFallback(formats); err != nil {
		return nil, err
	}
	kind := "doc"
	if bytes.HasPrefix(bytes.TrimSpace(body), []byte("%PDF-")) {
		kind = "pdf"
	} else if bytes.HasPrefix(body, []byte("PK\x03\x04")) {
		kind = "docx"
	}
	config, _ := formats[kind].(map[string]any)
	if config == nil {
		config = map[string]any{}
	}
	if kind == "pdf" {
		o, err := api.PDFOptionsFromConfig(config)
		if err != nil {
			return nil, err
		}
		return extractPDFBinary(ctx, body, source, o)
	}
	if kind == "docx" {
		description := api.ADPDocxToHTML(body)
		if description == "" {
			return nil, api.ErrInventory
		}
		text, err := api.DOMWordHTMLText(description)
		if err != nil {
			return nil, err
		}
		return api.ProjectDOMWord(text, description, source, config, true)
	}
	if len(body) > 20<<20 || !bytes.HasPrefix(body, []byte{0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1}) {
		return nil, api.ErrInventory
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	dir, err := os.MkdirTemp("", "jobseek-word-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	file := filepath.Join(dir, "source.doc")
	if err = os.WriteFile(file, body, 0600); err != nil {
		return nil, err
	}
	raw, err := pdfCommand(ctx, 20<<20, "antiword", "-m", "UTF-8.txt", "-w", "0", file)
	if err != nil {
		return nil, err
	}
	text := strings.TrimSpace(strings.ToValidUTF8(string(raw), "\ufffd"))
	if text == "" {
		return nil, api.ErrInventory
	}
	return api.ProjectDOMWord(text, api.DOMLegacyWordHTML(text), source, config, false)
}
