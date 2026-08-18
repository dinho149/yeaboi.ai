// CPython 3.11 textwrap semantics, for the slice argparse help rendering
// uses: `textwrap.wrap(text, width)` with the default TextWrapper settings,
// over text argparse has already whitespace-collapsed
// (`_whitespace_matcher.sub(' ', text).strip()`). That precondition matters:
// with runs of whitespace already reduced to single ASCII spaces,
// `_munge_whitespace` is the identity and the wordsep machinery only ever
// sees space-separated words — which is what lets `wordsep_re`'s lookbehind
// alternations (unavailable under RE2) be hand-rolled here.
//
// Python twin: Lib/textwrap.py — `wordsep_re`, `_split`, `_handle_long_word`
// and `_wrap_chunks`, defaults only (break_long_words=True,
// break_on_hyphens=True, drop_whitespace=True, no indents, no max_lines).
package argview

import (
	"strings"
	"unicode"
)

// isWordChar mirrors `\w` under re.UNICODE: letters, digits, underscore.
func isWordChar(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsDigit(r) || unicode.IsMark(r)
}

// isLetter mirrors `[^\d\W]` (a word char that is not a digit).
func isLetter(r rune) bool {
	return isWordChar(r) && !unicode.IsDigit(r)
}

// isWordPunct mirrors word_punct = `[\w!"'&.,?]`.
func isWordPunct(r rune) bool {
	return isWordChar(r) || strings.ContainsRune(`!"'&.,?`, r)
}

// hyphenRun is the length of the '-' run starting at i (0 if none).
func hyphenRun(runes []rune, i int) int {
	n := 0
	for i+n < len(runes) && runes[i+n] == '-' {
		n++
	}
	return n
}

// splitChunks mirrors wordsep_re.split over one space-free word: the word,
// cut after breakable hyphens and around ASCII em-dashes.
func splitChunks(word string) []string {
	runes := []rune(word)
	var chunks []string
	i := 0
	for i < len(runes) {
		// Top-level alternative: an em-dash run between words is its own
		// chunk — `(?<=word_punct) -{2,} (?=\w)`.
		if n := hyphenRun(runes, i); n >= 2 && i > 0 && isWordPunct(runes[i-1]) && i+n < len(runes) && isWordChar(runes[i+n]) {
			chunks = append(chunks, string(runes[i:i+n]))
			i += n
			continue
		}
		// `\S+?` with its lazy alternation: the shortest expansion at which
		// one of the three ends matches; the hyphen branch is tried first
		// and consumes the hyphen into the chunk.
		end := -1
		for j := i + 1; j <= len(runes); j++ {
			// hyphenated word: `-(?:(?<=lt{2}-)|(?<=lt-lt-))(?=lt-?lt)`.
			if j < len(runes) && runes[j] == '-' && hyphenRun(runes, j) == 1 {
				behind := (j >= 2 && isLetter(runes[j-1]) && isLetter(runes[j-2])) ||
					(j >= 3 && runes[j-2] == '-' && isLetter(runes[j-1]) && isLetter(runes[j-3]))
				ahead := j+1 < len(runes) && isLetter(runes[j+1]) &&
					(j+2 < len(runes) && (isLetter(runes[j+2]) ||
						(runes[j+2] == '-' && j+3 < len(runes) && isLetter(runes[j+3]))))
				if behind && ahead {
					end = j + 1
					break
				}
			}
			// end of word: `(?=\s|\Z)` — the caller splits on spaces first,
			// so only \Z remains.
			if j == len(runes) {
				end = j
				break
			}
			// em-dash ahead: `(?<=word_punct)(?=-{2,}\w)`.
			if n := hyphenRun(runes, j); n >= 2 && isWordPunct(runes[j-1]) && j+n < len(runes) && isWordChar(runes[j+n]) {
				end = j
				break
			}
		}
		chunks = append(chunks, string(runes[i:end]))
		i = end
	}
	return chunks
}

// split mirrors TextWrapper._split over collapsed text: words become chunk
// runs, and each single separating space is its own chunk.
func split(text string) []string {
	var chunks []string
	for i, word := range strings.Split(text, " ") {
		if i > 0 {
			chunks = append(chunks, " ")
		}
		if word != "" {
			chunks = append(chunks, splitChunks(word)...)
		}
	}
	return chunks
}

// handleLongWord mirrors TextWrapper._handle_long_word under the default
// break_long_words=True + break_on_hyphens=True: cut the chunk at the last
// hyphen inside the window when one (preceded by a non-hyphen) exists,
// else at the window edge. Python's slicing clamps out-of-range ends; the
// explicit clamp below is that.
func handleLongWord(chunks *[]string, curLine *[]string, curLen, width int) {
	spaceLeft := 1
	if width >= 1 {
		spaceLeft = width - curLen
	}
	chunk := []rune((*chunks)[len(*chunks)-1])
	end := spaceLeft
	if len(chunk) > spaceLeft {
		hyphen := -1
		for k := 0; k < spaceLeft && k < len(chunk); k++ {
			if chunk[k] == '-' {
				hyphen = k
			}
		}
		if hyphen > 0 {
			for _, c := range chunk[:hyphen] {
				if c != '-' {
					end = hyphen + 1
					break
				}
			}
		}
	}
	if end > len(chunk) {
		end = len(chunk)
	}
	if end < 0 {
		end = 0
	}
	*curLine = append(*curLine, string(chunk[:end]))
	(*chunks)[len(*chunks)-1] = string(chunk[end:])
}

// wrapCollapsed mirrors textwrap.wrap(text, width) over already-collapsed
// text with the default settings and no indents.
func wrapCollapsed(text string, width int) []string {
	chunks := split(text)
	// _wrap_chunks pops from the end.
	for i, j := 0, len(chunks)-1; i < j; i, j = i+1, j-1 {
		chunks[i], chunks[j] = chunks[j], chunks[i]
	}
	var lines []string
	for len(chunks) > 0 {
		var curLine []string
		curLen := 0
		if strings.TrimSpace(chunks[len(chunks)-1]) == "" && len(lines) > 0 {
			chunks = chunks[:len(chunks)-1]
		}
		for len(chunks) > 0 {
			l := len([]rune(chunks[len(chunks)-1]))
			if curLen+l <= width {
				curLine = append(curLine, chunks[len(chunks)-1])
				chunks = chunks[:len(chunks)-1]
				curLen += l
			} else {
				break
			}
		}
		if len(chunks) > 0 && len([]rune(chunks[len(chunks)-1])) > width {
			handleLongWord(&chunks, &curLine, curLen, width)
			curLen = 0
			for _, c := range curLine {
				curLen += len([]rune(c))
			}
		}
		if len(curLine) > 0 && strings.TrimSpace(curLine[len(curLine)-1]) == "" {
			curLine = curLine[:len(curLine)-1]
		}
		if len(curLine) > 0 {
			lines = append(lines, strings.Join(curLine, ""))
		}
	}
	return lines
}
