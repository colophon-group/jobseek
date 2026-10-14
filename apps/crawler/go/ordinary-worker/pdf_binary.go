package worker

import (
	"bytes"
	"context"
	"errors"
	"image/png"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

var errPDFBinary = errors.New("native PDF extraction failed")

type pdfBoundedOutput struct {
	bytes.Buffer
	remaining int
	failed    bool
}

func (b *pdfBoundedOutput) Write(p []byte) (int, error) {
	if b.failed || len(p) > b.remaining {
		b.failed = true
		return 0, errPDFBinary
	}
	n, err := b.Buffer.Write(p)
	b.remaining -= n
	return n, err
}

// ReadFrom must route through Write: embedding bytes.Buffer otherwise lets
// io.Copy bypass the output limit through its promoted ReaderFrom method.
func (b *pdfBoundedOutput) ReadFrom(r io.Reader) (int64, error) {
	return io.Copy(struct{ io.Writer }{b}, r)
}

func pdfCommand(ctx context.Context, limit int, name string, args ...string) ([]byte, error) {
	command := exec.CommandContext(ctx, name, args...)
	command.Env = []string{"PATH=" + os.Getenv("PATH"), "LC_ALL=C", "LANG=C"}
	out := &pdfBoundedOutput{remaining: limit}
	diagnostics := &pdfBoundedOutput{remaining: 8192}
	command.Stdout = out
	command.Stderr = diagnostics
	if err := command.Run(); err != nil || out.failed || diagnostics.failed {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errPDFBinary
	}
	return out.Bytes(), nil
}

type pdfPageSize struct{ width, height float64 }

func pdfOCRPageSizes(info []byte, scale int) ([]pdfPageSize, error) {
	pagesRE := regexp.MustCompile(`(?m)^Pages:\s+([0-9]+)\s*$`)
	m := pagesRE.FindSubmatch(info)
	if len(m) != 2 {
		return nil, errPDFBinary
	}
	pages, err := strconv.Atoi(string(m[1]))
	if err != nil || pages < 1 || pages > 20 {
		return nil, errPDFBinary
	}
	sizes := make([]pdfPageSize, pages)
	re := regexp.MustCompile(`(?m)^Page\s+([0-9]+)\s+size:\s+([0-9.eE+-]+)\s+x\s+([0-9.eE+-]+)\s+pts`)
	for _, m := range re.FindAllSubmatch(info, -1) {
		index, err := strconv.Atoi(string(m[1]))
		if err != nil || index < 1 || index > pages {
			return nil, errPDFBinary
		}
		w, e1 := strconv.ParseFloat(string(m[2]), 64)
		h, e2 := strconv.ParseFloat(string(m[3]), 64)
		if e1 != nil || e2 != nil || math.IsInf(w, 0) || math.IsInf(h, 0) || math.IsNaN(w) || math.IsNaN(h) || w <= 0 || h <= 0 || math.Ceil(w*float64(scale))*math.Ceil(h*float64(scale)) > 30_000_000 || sizes[index-1].width != 0 {
			return nil, errPDFBinary
		}
		sizes[index-1] = pdfPageSize{w, h}
	}
	for _, size := range sizes {
		if size.width == 0 {
			return nil, errPDFBinary
		}
	}
	return sizes, nil
}

// Native text extraction, Poppler and Tesseract execute as local processes under the crawler's
// unprivileged identity. Publisher URLs, headers and config never select a
// binary or command. Temporary documents disappear on every exit path.
func extractPDFBinary(ctx context.Context, body []byte, source string, o api.PDFOptions) (map[string]any, error) {
	if len(body) == 0 || len(body) > 40<<20 || !bytes.HasPrefix(bytes.TrimSpace(body), []byte("%PDF")) {
		return nil, errPDFBinary
	}
	ctx, cancel := context.WithTimeout(ctx, 180*time.Second)
	defer cancel()
	dir, err := os.MkdirTemp("", "jobseek-pdf-")
	if err != nil {
		return nil, errPDFBinary
	}
	defer os.RemoveAll(dir)
	file := filepath.Join(dir, "source.pdf")
	if os.WriteFile(file, body, 0o600) != nil {
		return nil, errPDFBinary
	}
	text, err := nativePDFText(ctx, body)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(string(text)) == "" && o.OCR {
		info, err := pdfCommand(ctx, 64<<10, "pdfinfo", "-f", "1", "-l", "20", file)
		if err != nil {
			return nil, err
		}
		sizes, err := pdfOCRPageSizes(info, o.OCRScale)
		if err != nil {
			return nil, err
		}
		pages := []string{}
		total := 0
		for i := range sizes {
			prefix := filepath.Join(dir, "page")
			imagePath := prefix + ".png"
			_, err := pdfCommand(ctx, 1024, "pdftoppm", "-f", strconv.Itoa(i+1), "-l", strconv.Itoa(i+1), "-r", strconv.Itoa(72*o.OCRScale), "-singlefile", "-png", file, prefix)
			if err != nil {
				return nil, err
			}
			f, err := os.Open(imagePath)
			if err != nil {
				return nil, errPDFBinary
			}
			stat, e1 := f.Stat()
			size, e2 := png.DecodeConfig(io.LimitReader(f, 64<<10))
			f.Close()
			if e1 != nil || e2 != nil || stat.Size() > 120<<20 || size.Width < 1 || size.Height < 1 || int64(size.Width)*int64(size.Height) > 30_000_000 {
				return nil, errPDFBinary
			}
			page, err := pdfCommand(ctx, 16<<20, "tesseract", imagePath, "stdout", "-l", o.OCRLanguages)
			os.Remove(imagePath)
			if err != nil {
				return nil, err
			}
			value := strings.TrimSpace(string(page))
			total += len(value) + 2
			if total > 16<<20 {
				return nil, errPDFBinary
			}
			if value != "" {
				pages = append(pages, value)
			}
		}
		text = []byte(strings.Join(pages, "\n\n"))
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	return api.ParsePDFText(string(text), source, o)
}

func nativePDFText(ctx context.Context, body []byte) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "go-pdf-text")
	command.Stdin = bytes.NewReader(body)
	command.Env = []string{"PATH=/usr/local/bin:/usr/bin:/bin", "GOMEMLIMIT=128MiB", "GOMAXPROCS=1"}
	output := &pdfBoundedOutput{remaining: 16 << 20}
	command.Stdout = output
	if err := command.Run(); err != nil || output.failed {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errPDFBinary
	}
	return output.Bytes(), nil
}
