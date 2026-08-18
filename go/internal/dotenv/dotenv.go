// Port of python-dotenv's main.py + variables.py (v1.2.2) — keep in lockstep;
// this is the half yeaboi/config.py actually calls: load_dotenv (project
// .env + ~/.yeaboi/.env, override=False, interpolate=True), set_key
// (quote_mode "always"), and find_dotenv. tests/parity/foundations/ replays
// a corpus of files and set_key scenarios against both sides.
//
// One documented deviation, decided in the W8 spec: find_dotenv in a source
// checkout walks up from the *calling file's* directory (a stack
// inspection Go cannot reproduce); for a frozen executable — which is what
// cmd/yeaboi is — python-dotenv itself falls back to walking up from the
// working directory, and fs_policy.py:98 already defines the project .env
// as `Path.cwd() / ".env"`. Find implements the frozen behaviour, and the
// parity dumper pins the Python side to it (sys.frozen) for the same reason.
package dotenv

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Lookup is a point read of an environment (os.environ or a layered stand-in).
type Lookup func(key string) (string, bool)

// Pair is one key with its raw or resolved value; a nil Value mirrors
// python-dotenv's None for a bare `KEY` statement.
type Pair struct {
	Key   string
	Value *string
}

// Pairs mirrors DotEnv.parse(): the bindings that carry a key, in order.
func Pairs(src string) []Pair {
	var out []Pair
	for _, b := range Parse(src) {
		if b.Key != nil {
			out = append(out, Pair{Key: *b.Key, Value: b.Value})
		}
	}
	return out
}

// _posix_variable: ${name} / ${name:-default}; a ':' not followed by '-'
// keeps the whole token literal (the name class excludes ':').
var posixVariable = regexp.MustCompile(`\$\{([^}:]*)(?::-([^}]*))?\}`)

// Resolve mirrors resolve_variables(values, override): each value's
// ${...} atoms resolve against the new values so far and the environment,
// the environment winning when override is false (the only mode config.py
// uses). A nil value stays nil.
func Resolve(pairs []Pair, environ Lookup, override bool) []Pair {
	newValues := make(map[string]*string, len(pairs))
	resolved := make([]Pair, 0, len(pairs))
	get := func(name, def string) string {
		fromNew := func() (string, bool) {
			v, ok := newValues[name]
			if !ok {
				return "", false
			}
			if v == nil { // a None value resolves to ""
				return "", true
			}
			return *v, true
		}
		if override {
			if v, ok := fromNew(); ok {
				return v
			}
			if v, ok := environ(name); ok {
				return v
			}
		} else {
			if v, ok := environ(name); ok {
				return v
			}
			if v, ok := fromNew(); ok {
				return v
			}
		}
		return def
	}
	for _, p := range pairs {
		if p.Value == nil {
			newValues[p.Key] = nil
			resolved = append(resolved, Pair{Key: p.Key})
			continue
		}
		value := *p.Value
		var b strings.Builder
		cursor := 0
		for _, m := range posixVariable.FindAllStringSubmatchIndex(value, -1) {
			b.WriteString(value[cursor:m[0]])
			name := value[m[2]:m[3]]
			def := ""
			if m[4] >= 0 {
				def = value[m[4]:m[5]]
			}
			b.WriteString(get(name, def))
			cursor = m[1]
		}
		b.WriteString(value[cursor:])
		result := b.String()
		newValues[p.Key] = &result
		resolved = append(resolved, Pair{Key: p.Key, Value: &result})
	}
	return resolved
}

// LoadInto mirrors load_dotenv(path, override=False) minus the os.environ
// mutation: it returns the keys the load would have added. environ must
// already see every earlier layer (process env + previous loads), because
// both the `k in os.environ` skip and the interpolation read it.
func LoadInto(path string, environ Lookup) map[string]string {
	src := ""
	if isFileOrFifo(path) {
		if data, err := os.ReadFile(path); err == nil {
			src = string(data)
		}
	}
	added := map[string]string{}
	for _, p := range Resolve(Pairs(src), environ, false) {
		if _, ok := environ(p.Key); ok {
			continue
		}
		if p.Value != nil {
			added[p.Key] = *p.Value
		}
	}
	return added
}

// Find mirrors find_dotenv() under frozen semantics (see the package
// comment): walk from dir to the filesystem root, first .env wins.
func Find(dir string) string {
	current, err := filepath.Abs(dir)
	if err != nil {
		return ""
	}
	for {
		candidate := filepath.Join(current, ".env")
		if isFileOrFifo(candidate) {
			return candidate
		}
		parent := filepath.Dir(current)
		if parent == current {
			return ""
		}
		current = parent
	}
}

// isFileOrFifo mirrors _is_file_or_fifo.
func isFileOrFifo(path string) bool {
	fi, err := os.Stat(path)
	if err != nil {
		return false
	}
	return fi.Mode().IsRegular() || fi.Mode()&fs.ModeNamedPipe != 0
}

// SetKey mirrors set_key(path, key, value) at its defaults (quote_mode
// "always", export=False): every statement whose key matches is replaced by
// `KEY='value'` (single quotes escaped), everything else — comments, junk
// lines, other keys — is written back verbatim, and a missing key is
// appended. A pre-existing file keeps its permission bits (rewrite chmods
// the temp file back); a new file gets 0600, the mode NamedTemporaryFile
// created it with.
func SetKey(path, key, value string) error {
	lineOut := key + "='" + strings.ReplaceAll(value, "'", `\'`) + "'\n"

	src := ""
	var originalMode fs.FileMode
	hadMode := false
	data, err := os.ReadFile(path)
	switch {
	case err == nil:
		src = string(data)
		if fi, lerr := os.Lstat(path); lerr == nil && fi.Mode().IsRegular() {
			originalMode = fi.Mode().Perm()
			hadMode = true
		}
	case errors.Is(err, fs.ErrNotExist):
		// rewrite() starts from an empty source and creates the file.
	default:
		return err
	}

	var b strings.Builder
	replaced := false
	missingNewline := false
	for _, m := range Parse(src) {
		if m.Key != nil && *m.Key == key {
			b.WriteString(lineOut)
			replaced = true
			continue
		}
		b.WriteString(m.Original)
		missingNewline = !strings.HasSuffix(m.Original, "\n")
	}
	if !replaced {
		if missingNewline {
			b.WriteString("\n")
		}
		b.WriteString(lineOut)
	}

	if err := writeFileAtomic(path, []byte(b.String()), 0o600); err != nil {
		return err
	}
	if hadMode {
		return os.Chmod(path, originalMode)
	}
	return nil
}

// writeFileAtomic mirrors rewrite()'s NamedTemporaryFile + shutil.move: the
// new content lands in a same-directory temp file that is renamed over path,
// so an error or a kill mid-write leaves the original file — the one holding
// every configured API key — completely intact. It also means a symlink at
// path is replaced by a regular file rather than written through, exactly
// as shutil.move replaces it.
func writeFileAtomic(path string, data []byte, mode fs.FileMode) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), ".env-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName) // no-op once the rename has happened
	if err := tmp.Chmod(mode); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}
