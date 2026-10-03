package worker

import (
	"bufio"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"errors"
	"io"
	"strings"
)

// Decoding is lazy: response accounting and host status precede body/decoder
// errors, as with httpx's tracked raw stream. Bytes are counted before decoding.
type decodedBody struct {
	raw       *observedBody
	reader    io.Reader
	encoding  string
	closers   []io.Closer
	started   bool
	initError error
}

func newDecodedBody(raw *observedBody, encoding string) io.ReadCloser {
	return &decodedBody{raw: raw, encoding: encoding}
}

func (b *decodedBody) Read(p []byte) (int, error) {
	if !b.started {
		b.started = true
		b.reader = b.raw
		encodings := strings.Split(b.encoding, ",")
		for i := len(encodings) - 1; i >= 0; i-- {
			var decoder io.ReadCloser
			var err error
			switch strings.TrimSpace(strings.ToLower(encodings[i])) {
			case "gzip":
				var gzipReader *gzip.Reader
				buffer := bufio.NewReader(b.reader)
				prefix, _ := buffer.Peek(10)
				if len(prefix) >= 2 && (prefix[0] != 0x1f || prefix[1] != 0x8b) || len(prefix) >= 4 && (prefix[2] != 8 || prefix[3]&0xe0 != 0) {
					err = gzip.ErrHeader
				} else {
					gzipReader, err = gzip.NewReader(buffer)
				}
				if err == nil {
					gzipReader.Multistream(false) // httpx consumes only the first member
					decoder = gzipReader
				}
			case "deflate":
				buffer := bufio.NewReader(b.reader)
				prefix, peekError := buffer.Peek(2)
				if peekError != nil {
					err = peekError
				} else if prefix[0]&15 == 8 && (int(prefix[0])<<8|int(prefix[1]))%31 == 0 {
					decoder, err = zlib.NewReader(buffer)
				} else {
					decoder = flate.NewReader(buffer)
				}
			}
			if err != nil {
				b.initError = err
				break
			}
			if decoder != nil {
				b.reader = decoder
				b.closers = append(b.closers, decoder)
			}
		}
	}
	if b.initError != nil {
		return 0, b.finishRead(b.initError)
	}
	n, err := b.reader.Read(p)
	return n, b.finishRead(err)
}

func (b *decodedBody) finishRead(err error) error {
	if err != io.EOF && !errors.Is(err, io.ErrUnexpectedEOF) {
		return err
	}
	// zlib.decompressobj.flush does not require a final compression trailer.
	// Match that decoder behavior only for a complete HTTP body, never for
	// missing wire bytes or a canceled/oversized response. Drain raw trailing
	// members/bytes, matching httpx's raw iteration and encoded-byte meter.
	_, drainError := io.Copy(io.Discard, b.raw)
	if b.raw.readError != nil {
		return b.raw.readError
	}
	if drainError != nil {
		return drainError
	}
	return io.EOF
}

func (b *decodedBody) Close() error {
	for i := len(b.closers) - 1; i >= 0; i-- {
		_ = b.closers[i].Close()
	}
	return b.raw.Close()
}
