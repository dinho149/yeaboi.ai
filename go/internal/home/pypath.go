// Port of the pathlib/posixpath semantics src/yeaboi/paths.py leans on:
// PurePosixPath string normalisation, Path.expanduser, Path.home and the `/`
// join operator — keep in lockstep; the Python stdlib is the reference
// implementation and tests/parity/foundations/ diffs whole-surface output.
//
// paths.py never calls resolve(), so symlinks and ".." are left alone here
// too: normalisation is purely lexical.
package home

import (
	"errors"
	"os/user"
	"strings"
)

// errNoHome mirrors the RuntimeError pathlib raises when a home directory
// cannot be determined ("Could not determine home directory.").
var errNoHome = errors.New("could not determine home directory")

// Env looks up an environment variable, distinguishing unset from empty the
// way os.environ membership does (posixpath.expanduser checks `'HOME' not in
// os.environ`, so "" and absent behave differently). os.LookupEnv satisfies
// it; tests substitute a map.
type Env func(key string) (string, bool)

// normPath mirrors str(PurePosixPath(s)): collapse repeated slashes (except a
// leading "//", which POSIX reserves and pathlib preserves — "///" collapses
// to "/"), drop empty and "." components, keep "..", no trailing slash, and
// an empty path becomes ".".
func normPath(s string) string {
	root := ""
	if strings.HasPrefix(s, "/") {
		if strings.HasPrefix(s, "//") && !strings.HasPrefix(s, "///") {
			root = "//"
		} else {
			root = "/"
		}
	}
	var parts []string
	for _, p := range strings.Split(s, "/") {
		if p == "" || p == "." {
			continue
		}
		parts = append(parts, p)
	}
	out := strings.Join(parts, "/")
	switch {
	case root != "":
		return root + out
	case out == "":
		return "."
	default:
		return out
	}
}

// joinPath mirrors the Path `/` operator for plain (relative, single-segment)
// names, which is the only way paths.py ever joins: Path(".")/"x" is "x", and
// the roots "/" and "//" are the only normalised strings ending in a slash.
func joinPath(base string, names ...string) string {
	out := base
	for _, n := range names {
		switch {
		case out == ".":
			out = n
		case strings.HasSuffix(out, "/"):
			out += n
		default:
			out = out + "/" + n
		}
	}
	return out
}

// expandUser mirrors Path.expanduser() on an already-normalised path: only a
// non-rooted path whose first component starts with "~" expands, via
// posixpath.expanduser's rules — HOME wins for "~" when present (even empty),
// else the passwd database; "~user" is always a passwd lookup; the home is
// rstrip("/")-ed and an empty result becomes "/". Where posixpath would hand
// back a path still starting with "~" (unknown user, HOME itself starting
// with "~"), pathlib raises RuntimeError — mirrored as errNoHome.
func expandUser(p string, env Env) (string, error) {
	if strings.HasPrefix(p, "/") {
		return p, nil
	}
	first, rest, _ := strings.Cut(p, "/")
	if !strings.HasPrefix(first, "~") {
		return p, nil
	}
	var userhome string
	if first == "~" {
		if v, ok := env("HOME"); ok {
			userhome = v
		} else {
			u, err := user.Current()
			if err != nil {
				return "", errNoHome
			}
			userhome = u.HomeDir
		}
	} else {
		u, err := user.Lookup(first[1:])
		if err != nil {
			return "", errNoHome
		}
		userhome = u.HomeDir
	}
	homedir := strings.TrimRight(userhome, "/")
	if homedir == "" {
		homedir = "/"
	}
	if strings.HasPrefix(homedir, "~") {
		return "", errNoHome
	}
	// pathlib re-parses [homedir] + parts[1:] as parts, so a root homedir
	// gains no spurious "//" — join part-wise, not by string concatenation.
	if rest == "" {
		return normPath(homedir), nil
	}
	return joinPath(normPath(homedir), rest), nil
}

// homeDir mirrors Path.home(), which is Path("~").expanduser().
func homeDir(env Env) (string, error) {
	return expandUser("~", env)
}
