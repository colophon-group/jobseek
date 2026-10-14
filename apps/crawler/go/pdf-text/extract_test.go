package pdftext

import (
	"bytes"
	"errors"
	"fmt"
	"strings"
	"testing"
)

func fixturePDF(streams []string, pages int) []byte {
	objects := []string{"<< /Type /Catalog /Pages 2 0 R >>", fmt.Sprintf("<< /Type /Pages /Kids [3 0 R] /Count %d >>", pages), "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
	refs := []string{}
	for i, stream := range streams {
		refs = append(refs, fmt.Sprintf("%d 0 R", i+5))
		objects = append(objects, fmt.Sprintf("<< /Length %d >>\nstream\n%s\nendstream", len(stream), stream))
	}
	objects[2] = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents [" + strings.Join(refs, " ") + "] >>"
	var b bytes.Buffer
	b.WriteString("%PDF-1.4\n")
	offsets := []int{0}
	for i, obj := range objects {
		offsets = append(offsets, b.Len())
		fmt.Fprintf(&b, "%d 0 obj\n%s\nendobj\n", i+1, obj)
	}
	xref := b.Len()
	fmt.Fprintf(&b, "xref\n0 %d\n0000000000 65535 f \n", len(offsets))
	for _, off := range offsets[1:] {
		fmt.Fprintf(&b, "%010d 00000 n \n", off)
	}
	fmt.Fprintf(&b, "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n", len(offsets), xref)
	return b.Bytes()
}

func TestContentStreamOperandsCrossSeams(t *testing.T) {
	text, err := Extract(fixturePDF([]string{"BT /F1", "12 Tf 1 0 0 1 10 10 Tm (Original role) Tj ET"}, 1))
	if err != nil || text != "Original role" {
		t.Fatalf("text=%q err=%v", text, err)
	}
}
func TestMalformedInputCannotReturnPartialText(t *testing.T) {
	for _, body := range [][]byte{nil, []byte("not PDF"), []byte("%PDF-1.4 broken"), fixturePDF([]string{"BT /F1 12 Tf (Partial) Tj /F1 Tf ET"}, 1)} {
		text, err := Extract(body)
		if err == nil || text != "" {
			t.Fatalf("partial text=%q err=%v", text, err)
		}
	}
}
func TestPageLimit(t *testing.T) {
	text, err := Extract(fixturePDF([]string{""}, 513))
	if !errors.Is(err, ErrLimit) || text != "" {
		t.Fatalf("text=%q err=%v", text, err)
	}
}
func TestDecompressedStreamLimit(t *testing.T) {
	text, err := Extract(fixturePDF([]string{strings.Repeat(" ", 16<<20+1)}, 1))
	if !errors.Is(err, ErrLimit) || text != "" {
		t.Fatalf("text length=%d err=%v", len(text), err)
	}
}
func TestOperatorLimit(t *testing.T) {
	text, err := Extract(fixturePDF([]string{strings.Repeat("q Q\n", 500001)}, 1))
	if !errors.Is(err, ErrLimit) || text != "" {
		t.Fatalf("text length=%d err=%v", len(text), err)
	}
}

func TestBlankPageWithEmptyContents(t *testing.T) {
	body := fixturePDF(nil, 1)
	text, err := Extract(body)
	if err != nil || text != "" {
		t.Fatalf("text=%q err=%v", text, err)
	}
}

func TestGraphicsStateRestoresFontAndSize(t *testing.T) {
	baseline := "BT /F1 12 Tf 1 0 0 1 10 10 Tm (First) Tj 40 0 Td (Second) Tj ET"
	changed := "BT /F1 12 Tf 1 0 0 1 10 10 Tm (First) Tj q /F1 1 Tf Q 40 0 Td (Second) Tj ET"
	a, e1 := Extract(fixturePDF([]string{baseline}, 1))
	b, e2 := Extract(fixturePDF([]string{changed}, 1))
	if e1 != nil || e2 != nil || a != b {
		t.Fatalf("restored output differs: %q %q (%v %v)", a, b, e1, e2)
	}
}
