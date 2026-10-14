package main

import (
	pdftext "github.com/colophon-group/jobseek/apps/crawler/go/pdf-text"
	"io"
	"os"
)

func main() {
	if len(os.Args) != 1 {
		os.Exit(2)
	}
	body, e := io.ReadAll(io.LimitReader(os.Stdin, 40<<20+1))
	if e != nil || len(body) > 40<<20 {
		os.Exit(2)
	}
	text, e := pdftext.Extract(body)
	if e != nil {
		os.Exit(1)
	}
	if _, e = os.Stdout.WriteString(text); e != nil {
		os.Exit(1)
	}
}
