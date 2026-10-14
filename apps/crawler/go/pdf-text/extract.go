package pdftext

import (
	"bytes"
	"errors"
	pdf "github.com/colophon-group/jobseek/apps/crawler/go/pdf-text/internal/pdf"
	"strings"
)

var ErrPDF = errors.New("native PDF extraction failed")
var ErrLimit = errors.New("native PDF extraction limit")

func Extract(body []byte) (text string, err error) {
	defer func() {
		if failure := recover(); failure != nil {
			text = ""
			err = ErrPDF
			if failure == ErrLimit {
				err = ErrLimit
			}
		}
	}()
	if len(body) == 0 || len(body) > 40<<20 || !bytes.HasPrefix(bytes.TrimSpace(body), []byte("%PDF")) {
		return "", ErrPDF
	}
	reader, e := pdf.NewReader(bytes.NewReader(body), int64(len(body)))
	if e != nil {
		return "", ErrPDF
	}
	pages := reader.NumPage()
	if pages < 1 || pages > 512 {
		return "", ErrLimit
	}
	out := []string{}
	size := 0
	for n := 1; n <= pages; n++ {
		page := extract(reader.Page(n))
		size += len(page) + 2
		if size > 16<<20 {
			return "", ErrLimit
		}
		if strings.TrimSpace(page) != "" {
			out = append(out, page)
		}
	}
	return strings.TrimSpace(strings.Join(out, "\n\n")), nil
}
