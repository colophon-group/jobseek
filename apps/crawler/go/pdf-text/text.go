package pdftext

import (
	"bytes"
	pdf "github.com/colophon-group/jobseek/apps/crawler/go/pdf-text/internal/pdf"
	"io"
	"math"
	"strings"
)

type matrix [6]float64

func mult(a, b matrix) matrix {
	return matrix{a[0]*b[0] + a[1]*b[2], a[0]*b[1] + a[1]*b[3], a[2]*b[0] + a[3]*b[2], a[2]*b[1] + a[3]*b[3], a[4]*b[0] + a[5]*b[2] + b[4], a[4]*b[1] + a[5]*b[3] + b[5]}
}
func glyphWidth(f pdf.Font, code int) float64 {
	if f.V.Key("Subtype").Name() != "Type0" {
		return f.Width(code)
	}
	d := f.V.Key("DescendantFonts").Index(0)
	w := d.Key("W")
	for i := 0; i < w.Len(); {
		first := int(w.Index(i).Int64())
		i++
		v := w.Index(i)
		i++
		if v.Kind() == pdf.Array {
			if code >= first && code < first+v.Len() {
				return v.Index(code - first).Float64()
			}
		} else {
			last := int(v.Int64())
			value := w.Index(i).Float64()
			i++
			if code >= first && code <= last {
				return value
			}
		}
	}
	if x := d.Key("DW"); x.Kind() != pdf.Null {
		return x.Float64()
	}
	return 1000
}
func textWidth(f pdf.Font, raw string) float64 {
	total := 0.
	if f.V.Key("Encoding").Name() == "Identity-H" {
		for i := 0; i+1 < len(raw); i += 2 {
			total += glyphWidth(f, int(raw[i])<<8|int(raw[i+1]))
		}
	} else {
		for _, ch := range []byte(raw) {
			total += glyphWidth(f, int(ch))
		}
	}
	return total
}
func spaceWidth(f pdf.Font, enc pdf.TextEncoding) float64 {
	if f.V.Key("Encoding").Name() == "Identity-H" {
		for code := 0; code < 65536; code++ {
			if enc.Decode(string([]byte{byte(code >> 8), byte(code)})) == " " {
				return glyphWidth(f, code) / 2
			}
		}
		// PdfReader page extraction overrides unmapped spaces with its
		// public default space_width=200, then uses half a space.
		return 100

	}
	return glyphWidth(f, 32) / 2
}
func extract(p pdf.Page) string {
	identity := matrix{1, 0, 0, 1, 0, 0}
	cm, tm, prev := identity, identity, identity
	stack := []matrix{}
	fonts := map[string]pdf.Font{}
	for _, name := range p.Fonts() {
		fonts[name] = p.Font(name)
	}
	var font pdf.Font
	var enc pdf.TextEncoding
	size, space, width, height, leading := 12., 125., 0., 0., 0.
	var out, text string
	flush := func() {
		if len(out)+len(text) > 16<<20 {
			panic(ErrLimit)
		}
		out += text
		text = ""
	}
	post := func(w float64) {
		m := mult(tm, cm)
		dx, dy := m[4]-prev[4], m[5]-prev[5]
		sy := math.Hypot(m[2], m[3])
		py := math.Hypot(prev[2], prev[3])
		px := math.Hypot(prev[0], prev[1])
		all := out + text
		if len(all) > 0 {
			if math.Abs(dy) > 0.8*math.Min(height*py, size*sy) {
				if !strings.HasSuffix(all, "\n") {
					out += text + "\n"
					text = ""
				}
			} else if dx >= (size*space/1000+w)*px && !strings.HasSuffix(all, " ") {
				text += " "
			}
		}
		prev = m
	}
	show := func(raw string) {
		if enc != nil {
			text += enc.Decode(raw)
		} else {
			text += raw
		}
		width += textWidth(font, raw) * size
		height = size
		post(0)
	}
	nextLine := func() { tm[4] -= leading * tm[2]; tm[5] -= leading * tm[3]; w := width / 1000; width = 0; post(w) }
	handle := func(stk *pdf.Stack, op string) {
		args := make([]pdf.Value, stk.Len())
		for i := len(args) - 1; i >= 0; i-- {
			args[i] = stk.Pop()
		}
		switch op {
		case "q":
			stack = append(stack, cm)
		case "Q":
			if len(stack) > 0 {
				cm = stack[len(stack)-1]
				stack = stack[:len(stack)-1]
			}
		case "cm":
			flush()
			var a matrix
			for i := range a {
				a[i] = args[i].Float64()
			}
			cm = mult(a, cm)
		case "BT":
			tm = identity
			flush()
		case "ET":
			flush()
		case "Tf":
			if len(args) != 2 {

				panic(ErrPDF)
			}
			flush()
			font = fonts[args[0].Name()]
			enc = font.Encoder()
			size = args[1].Float64()
			space = spaceWidth(font, enc)
			if space == 0 {
				space = 125
			}
		case "Tm":
			for i := range tm {
				tm[i] = args[i].Float64()
			}
			w := width / 1000
			width = 0
			post(w)
		case "Td":
			x, y := args[0].Float64(), args[1].Float64()
			tm[4] += x*tm[0] + y*tm[2]
			tm[5] += x*tm[1] + y*tm[3]
			w := width / 1000
			width = 0
			post(w)
		case "TL":
			leading = args[0].Float64()
		case "TD":
			leading = -args[1].Float64()
			x, y := args[0].Float64(), args[1].Float64()
			tm[4] += x*tm[0] + y*tm[2]
			tm[5] += x*tm[1] + y*tm[3]
			w := width / 1000
			width = 0
			post(w)
		case "T*":
			nextLine()
		case "Tj":
			show(args[0].RawString())
		case "TJ":
			if len(args) == 0 {
				return
			}
			v := args[0]
			for i := 0; i < v.Len(); i++ {
				x := v.Index(i)
				if x.Kind() == pdf.String {
					show(x.RawString())
				} else if math.Abs(x.Float64()) >= space*0.95 && len(text) > 0 && !strings.HasSuffix(text, " ") {
					text += " "
					height = size
					post(0)
				}
			}
		case "'":
			nextLine()
			show(args[0].RawString())
		case "\"":
			nextLine()
			show(args[2].RawString())
		case "Do":
			flush()
			if len(out) > 0 && !strings.HasSuffix(out, "\n") {
				out += "\n"
			}
		}
	}
	contents := p.V.Key("Contents")
	if contents.Kind() == pdf.Null {
		return ""
	}
	streams := []pdf.Value{contents}
	if contents.Kind() == pdf.Array {
		streams = nil
		for i := 0; i < contents.Len(); i++ {
			streams = append(streams, contents.Index(i))
		}
	}
	var joined bytes.Buffer
	for _, v := range streams {
		reader := v.Reader()
		body, err := io.ReadAll(io.LimitReader(reader, 16<<20+1))
		reader.Close()
		if err != nil || len(body) > 16<<20 || joined.Len()+len(body) > 16<<20 {
			panic(ErrLimit)
		}
		joined.Write(body)
		joined.WriteByte('\n')
	}
	operations := 0
	pdf.InterpretReader(bytes.NewReader(joined.Bytes()), func(stk *pdf.Stack, op string) {
		operations++
		if operations > 1000000 {
			panic(ErrLimit)
		}
		handle(stk, op)
		if len(out)+len(text) > 16<<20 {
			panic(ErrLimit)
		}
	})
	flush()
	if len(out) > 16<<20 {
		panic(ErrLimit)
	}
	return out
}
