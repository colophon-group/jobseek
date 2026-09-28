package enrichment

// Prediction and compressed-model decoding follow fasttext-predict 0.9.2.4
// (Facebook, Inc., MIT). See models/README.md for code/model attribution.
import (
	"bytes"
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"fmt"
	"io"
	"math"
	"regexp"
	"strings"
	"sync"
	"unicode"
)

//go:embed models/lid.176.ftz
var languageModelBytes []byte

const languageModelSHA = "8f3472cfe8738a7b6099e8e999c3cbfae0dcd15696aac7d7738a8039db603e83"

type languageEntry struct {
	word     string
	count    int64
	kind     int8
	subwords []int
}
type languageNode struct {
	left, right int
	count       int64
}
type languageModel struct {
	entries                  []languageEntry
	words                    map[string]int
	pruned                   map[uint32]int
	codes, normCodes         []byte
	centroids, norms, output []float32
	tree                     []languageNode
}

var residentLanguageModel = sync.OnceValues(func() (*languageModel, error) { return loadLanguageModel(languageModelBytes) })

// Only the checksum-pinned production model is accepted. This deliberately
// rejects an incompatible model instead of guessing its format or downloading.
func loadLanguageModel(data []byte) (*languageModel, error) {
	if fmt.Sprintf("%x", sha256.Sum256(data)) != languageModelSHA {
		return nil, fmt.Errorf("language model checksum mismatch")
	}
	r := bytes.NewReader(data)
	read := func(v any) error { return binary.Read(r, binary.LittleEndian, v) }
	var header [2]int32
	if err := read(&header); err != nil || header != [2]int32{793712314, 12} {
		return nil, fmt.Errorf("invalid language model header")
	}
	var args [12]int32
	var sampling float64
	if err := read(&args); err != nil {
		return nil, err
	}
	if err := read(&sampling); err != nil {
		return nil, err
	}
	if args != [12]int32{16, 5, 5, 1000, 5, 1, 1, 3, 2000000, 2, 4, 100} {
		return nil, fmt.Errorf("unsupported language model arguments")
	}
	var sizes [3]int32
	var tokens, pruneSize int64
	if err := read(&sizes); err != nil {
		return nil, err
	}
	if err := read(&tokens); err != nil {
		return nil, err
	}
	if err := read(&pruneSize); err != nil {
		return nil, err
	}
	if sizes != [3]int32{7411, 7235, 176} || pruneSize != 42765 {
		return nil, fmt.Errorf("unsupported language dictionary")
	}
	m := &languageModel{words: map[string]int{}, pruned: map[uint32]int{}, entries: make([]languageEntry, sizes[0])}
	for i := range m.entries {
		var w strings.Builder
		for {
			b, err := r.ReadByte()
			if err != nil {
				return nil, err
			}
			if b == 0 {
				break
			}
			w.WriteByte(b)
		}
		e := &m.entries[i]
		e.word = w.String()
		if err := read(&e.count); err != nil {
			return nil, err
		}
		if err := read(&e.kind); err != nil {
			return nil, err
		}
		m.words[e.word] = i
	}
	for range pruneSize {
		var p [2]int32
		if err := read(&p); err != nil {
			return nil, err
		}
		if p[0] < 0 || p[0] >= 2000000 || p[1] < 0 || p[1] >= 42765 {
			return nil, fmt.Errorf("invalid language prune index")
		}
		m.pruned[uint32(p[0])] = int(p[1]) + 7235
	}
	var quantized, qnorm bool
	var rows, dim int64
	var codeSize int32
	if err := read(&quantized); err != nil {
		return nil, err
	}
	if err := read(&qnorm); err != nil {
		return nil, err
	}
	if err := read(&rows); err != nil {
		return nil, err
	}
	if err := read(&dim); err != nil {
		return nil, err
	}
	if err := read(&codeSize); err != nil {
		return nil, err
	}
	if !quantized || !qnorm || rows != 50000 || dim != 16 || codeSize != 400000 {
		return nil, fmt.Errorf("unsupported quantized language input")
	}
	m.codes = make([]byte, codeSize)
	if _, err := io.ReadFull(r, m.codes); err != nil {
		return nil, err
	}
	var pq [4]int32
	if err := read(&pq); err != nil {
		return nil, err
	}
	if pq != [4]int32{16, 8, 2, 2} {
		return nil, fmt.Errorf("unsupported language quantizer")
	}
	m.centroids = make([]float32, 16*256)
	if err := read(&m.centroids); err != nil {
		return nil, err
	}
	m.normCodes = make([]byte, rows)
	if _, err := io.ReadFull(r, m.normCodes); err != nil {
		return nil, err
	}
	if err := read(&pq); err != nil {
		return nil, err
	}
	if pq != [4]int32{1, 1, 1, 1} {
		return nil, fmt.Errorf("unsupported language norm quantizer")
	}
	m.norms = make([]float32, 256)
	if err := read(&m.norms); err != nil {
		return nil, err
	}
	var qout bool
	if err := read(&qout); err != nil {
		return nil, err
	}
	if err := read(&rows); err != nil {
		return nil, err
	}
	if err := read(&dim); err != nil {
		return nil, err
	}
	if qout || rows != 176 || dim != 16 {
		return nil, fmt.Errorf("unsupported language output")
	}
	m.output = make([]float32, rows*dim)
	if err := read(&m.output); err != nil {
		return nil, err
	}
	if r.Len() != 0 {
		return nil, fmt.Errorf("trailing language model bytes")
	}
	for i := 0; i < 7235; i++ {
		m.entries[i].subwords = []int{i}
		if m.entries[i].word != "</s>" {
			m.entries[i].subwords = m.subwords("<"+m.entries[i].word+">", m.entries[i].subwords)
		}
	}
	m.tree = make([]languageNode, 351)
	for i := range m.tree {
		m.tree[i] = languageNode{-1, -1, 1000000000000000}
		if i < 176 {
			m.tree[i].count = m.entries[7235+i].count
		}
	}
	leaf, node := 175, 176
	for i := 176; i < 351; i++ {
		var children [2]int
		for j := range children {
			if leaf >= 0 && m.tree[leaf].count < m.tree[node].count {
				children[j] = leaf
				leaf--
			} else {
				children[j] = node
				node++
			}
		}
		m.tree[i] = languageNode{children[0], children[1], m.tree[children[0]].count + m.tree[children[1]].count}
	}
	return m, nil
}

// fastText's released models hash signed UTF-8 bytes, including overflow.
func languageHash(s string) uint32 {
	h := uint32(2166136261)
	for i := 0; i < len(s); i++ {
		h = (h ^ uint32(int32(int8(s[i])))) * 16777619
	}
	return h
}
func (m *languageModel) subwords(w string, out []int) []int {
	for i := 0; i < len(w); i++ {
		if w[i]&0xc0 == 0x80 {
			continue
		}
		for j, n := i, 1; j < len(w) && n <= 4; n++ {
			j++
			for j < len(w) && w[j]&0xc0 == 0x80 {
				j++
			}
			if n >= 2 {
				if row, ok := m.pruned[languageHash(w[i:j])%2000000]; ok {
					out = append(out, row)
				}
			}
		}
	}
	return out
}
func languageDelimiter(r rune) bool {
	return r == ' ' || r == '\r' || r == '\t' || r == '\v' || r == '\f' || r == 0
}

func languageInput(s string) string {
	// fast-langdetect 1.0.1's default max_input_length is 80, after LF
	// replacement and before uppercase normalization. Preserve this separately
	// from the crawler's 500-codepoint chunk/primary limits.
	r := []rune(strings.ReplaceAll(s, "\n", " "))
	if len(r) > 80 {
		r = r[:80]
	}
	upper, letters, hasCased, allUpper := 0, 0, false, true
	for _, c := range r {
		if c >= 'A' && c <= 'Z' {
			upper++
		}
		if c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' {
			letters++
		}
		if unicode.IsUpper(c) || unicode.IsLower(c) || unicode.IsTitle(c) || unicode.Is(unicode.Properties["Other_Uppercase"], c) || unicode.Is(unicode.Properties["Other_Lowercase"], c) {
			hasCased = true
			if !unicode.IsUpper(c) && !unicode.Is(unicode.Properties["Other_Uppercase"], c) {
				allUpper = false
			}
		}
	}
	s = string(r)
	if hasCased && allUpper || float64(upper) > .8*float64(letters) && len(r) > 5 {
		return lower(s)
	}
	return s
}
func languageLog(x float32) float32 { return float32(math.Log(float64(x) + 1e-5)) }
func (m *languageModel) predict(text string) (string, float32) {
	words := []int{}
	input := languageInput(text)
	ended := false
	for _, w := range strings.FieldsFunc(input, languageDelimiter) {
		if id, ok := m.words[w]; ok {
			if m.entries[id].kind == 0 {
				words = append(words, m.entries[id].subwords...)
			}
		} else if !strings.HasPrefix(w, "__label__") {
			words = m.subwords("<"+w+">", words)
		}
		if w == "</s>" {
			ended = true
			break
		}
	}
	// FastText.py appends LF; readWord emits EOS unless an explicit EOS already
	// ended the line. EOS appears once even for an otherwise empty prediction.
	if !ended {
		words = append(words, m.entries[m.words["</s>"]].subwords...)
	}
	if len(words) == 0 {
		return "", 0
	}
	var hidden [16]float32
	for _, row := range words {
		norm := m.norms[m.normCodes[row]]
		for sub := 0; sub < 8; sub++ {
			centroid := (sub*256 + int(m.codes[row*8+sub])) * 2
			for j := 0; j < 2; j++ {
				product := float32(norm * m.centroids[centroid+j])
				hidden[sub*2+j] = float32(hidden[sub*2+j] + product)
			}
		}
	}
	scale := float32(1.0 / float64(len(words)))
	for i := range hidden {
		hidden[i] = float32(hidden[i] * scale)
	}
	best, score := -1, float32(math.Inf(-1))
	minimum := languageLog(0)
	var visit func(int, float32)
	visit = func(node int, s float32) {
		if s < minimum || s < score {
			return
		}
		branch := m.tree[node]
		if branch.left == -1 {
			best, score = node, s
			return
		}
		var dot float32
		for j := range hidden {
			product := float32(m.output[(node-176)*16+j] * hidden[j])
			dot = float32(dot + product)
		}
		exp := float32(math.Exp(float64(-dot)))
		denom := float32(1 + exp)
		f := float32(1 / float64(denom))
		visit(branch.left, float32(s+languageLog(float32(1-f))))
		visit(branch.right, float32(s+languageLog(f)))
	}
	visit(350, 0)
	if best < 0 {
		return "", 0
	}
	prob := float32(math.Exp(float64(score)))
	if prob > 1 {
		prob = 1
	}
	return strings.ReplaceAll(m.entries[best+7235].word, "__label__", ""), prob
}

var languageTags = regexp.MustCompile(`<[^>]+>`)

func languagePlain(s string) string { return languageTags.ReplaceAllString(s, " ") }
func (m *languageModel) primary(description string) *string {
	r := []rune(languagePlain(description))
	if len(r) > 500 {
		r = r[:500]
	}
	plain := strings.TrimFunc(string(r), space)
	if plain == "" {
		return nil
	}
	lang, score := m.predict(plain)
	if lang != "" && score >= .3 {
		return &lang
	}
	return nil
}
func languageChunks(text string) []string {
	r := []rune(text)
	chunks := []string{}
	for start := 0; start < len(r); {
		end := start + 500
		if end >= len(r) {
			chunks = append(chunks, string(r[start:]))
			break
		}
		ws := -1
		for j := start; j < end; j++ {
			if r[j] == ' ' {
				ws = j
			}
		}
		if ws <= start {
			ws = end
		}
		chunks = append(chunks, string(r[start:ws]))
		start = ws + 1
	}
	return chunks
}
func (m *languageModel) all(description string) []string {
	plain := strings.TrimFunc(languagePlain(description), space)
	counts := map[string]int{}
	order := []string{}
	total := 0
	for _, chunk := range languageChunks(plain) {
		chunk = strings.TrimFunc(chunk, space)
		if len([]rune(chunk)) < 80 {
			continue
		}
		total++
		lang, score := m.predict(chunk)
		if lang != "" && score >= .3 {
			if counts[lang] == 0 {
				order = append(order, lang)
			}
			counts[lang]++
		}
	}
	out := []string{}
	for _, lang := range order {
		if float64(counts[lang])/float64(total) >= .15 {
			out = append(out, lang)
		}
	}
	return out
}
