package dom

import "strings"

// Cascadia's haschild predicate matches the CSS4 direct-child relational
// selector used by current rich rows. Translate parser input only; original
// metadata remains the ownership binding. Complex relative paths still refuse.
func richRowsCSS(selector string) (string, error) {
	var out strings.Builder
	quote := byte(0)
	for i := 0; i < len(selector); i++ {
		ch := selector[i]
		if ch == '\\' && i+1 < len(selector) {
			out.WriteByte(ch)
			i++
			out.WriteByte(selector[i])
			continue
		}
		if quote != 0 {
			out.WriteByte(ch)
			if ch == quote {
				quote = 0
			}
			continue
		}
		if ch == '\'' || ch == '"' {
			quote = ch
			out.WriteByte(ch)
			continue
		}
		if !strings.HasPrefix(selector[i:], ":has(") {
			out.WriteByte(ch)
			continue
		}
		start := i + 5
		depth := 1
		q := byte(0)
		end := start
		for ; end < len(selector); end++ {
			c := selector[end]
			if c == '\\' {
				end++
				continue
			}
			if q != 0 {
				if c == q {
					q = 0
				}
				continue
			}
			if c == '\'' || c == '"' {
				q = c
				continue
			}
			if c == '(' {
				depth++
			}
			if c == ')' {
				depth--
				if depth == 0 {
					break
				}
			}
		}
		if depth != 0 {
			return "", ErrRichRows
		}
		inner := trim(selector[start:end])
		if strings.HasPrefix(inner, ">") {
			inner = trim(inner[1:])
			if !richRowsCompound(inner) {
				return "", ErrRichRows
			}
			normalized, e := richRowsCSS(inner)
			if e != nil {
				return "", e
			}
			out.WriteString(":haschild(" + normalized + ")")
		} else {
			normalized, e := richRowsCSS(inner)
			if e != nil {
				return "", e
			}
			out.WriteString(":has(" + normalized + ")")
		}
		i = end
	}
	return out.String(), nil
}
func richRowsCompound(selector string) bool {
	if selector == "" {
		return false
	}
	quote := byte(0)
	brackets, parentheses := 0, 0
	for i := 0; i < len(selector); i++ {
		ch := selector[i]
		if ch == '\\' {
			i++
			continue
		}
		if quote != 0 {
			if ch == quote {
				quote = 0
			}
			continue
		}
		if ch == '\'' || ch == '"' {
			quote = ch
			continue
		}
		switch ch {
		case '[':
			brackets++
		case ']':
			brackets--
		case '(':
			parentheses++
		case ')':
			parentheses--
		}
		if brackets == 0 && parentheses == 0 && strings.ContainsRune(" >+~,\t\r\n\f", rune(ch)) {
			return false
		}
	}
	return brackets == 0 && parentheses == 0 && quote == 0
}
