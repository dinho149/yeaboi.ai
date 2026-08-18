// Port of get_aws_profile's ~/.aws/config autodetect (src/yeaboi/config.py)
// — keep in lockstep. Python parses the file with a default-strict
// configparser inside a bare `except Exception: pass`, so *any* parse error
// (duplicate sections or options, content before the first header, an
// undelimited line) means "no autodetected profile", never a crash. The
// parser below is the configparser subset that file format exercises:
// [section] headers (text after the closing bracket ignored), `=`/`:`
// delimited options with lowercased names, full-line #/; comments, indented
// continuation lines, and the DEFAULT section merging into has_option.
// Values are never read — has_option is the only question asked — so value
// interpolation and empty_lines_in_values stay unported.
package config

import (
	"os"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/home"
	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

type iniSection struct {
	name    string
	options map[string]bool
}

// parseAWSConfig returns the sections in file order (DEFAULT excluded, its
// options merged into every has_option check via the second return), or
// ok=false where configparser would raise.
func parseAWSConfig(text string) (sections []*iniSection, defaults map[string]bool, ok bool) {
	defaults = map[string]bool{}
	byName := map[string]*iniSection{}
	var current *iniSection
	inDefault := false
	sawSection := false
	var lastOption bool

	// configparser reads in text mode: universal newlines.
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	for _, line := range strings.Split(text, "\n") {
		stripped := pysem.Strip(line)
		// Blank and comment lines keep the continuation context alive
		// (configparser's empty_lines_in_values default).
		if stripped == "" {
			continue
		}
		if strings.HasPrefix(stripped, "#") || strings.HasPrefix(stripped, ";") {
			continue
		}
		if first, _ := utf8First(line); pysem.IsSpace(first) {
			// A continuation of the previous option's value; without one,
			// configparser raises. Values are ignored here.
			if lastOption {
				continue
			}
			return nil, nil, false
		}
		if strings.HasPrefix(stripped, "[") {
			end := strings.Index(stripped, "]")
			if end < 0 {
				return nil, nil, false // no SECTCRE match → ParsingError
			}
			header := stripped[1:end]
			if header == "" {
				return nil, nil, false
			}
			sawSection = true
			lastOption = false
			if header == "DEFAULT" {
				inDefault = true
				current = nil
				continue
			}
			inDefault = false
			if _, dup := byName[header]; dup {
				return nil, nil, false // DuplicateSectionError
			}
			current = &iniSection{name: header, options: map[string]bool{}}
			byName[header] = current
			sections = append(sections, current)
			continue
		}
		if !sawSection {
			return nil, nil, false // MissingSectionHeaderError
		}
		delim := strings.IndexAny(stripped, "=:")
		if delim < 0 {
			return nil, nil, false // no OPTCRE match → ParsingError
		}
		option := strings.ToLower(pysem.Strip(stripped[:delim]))
		if option == "" {
			return nil, nil, false
		}
		target := defaults
		if !inDefault {
			if current == nil {
				return nil, nil, false
			}
			target = current.options
		}
		if target[option] {
			return nil, nil, false // DuplicateOptionError
		}
		target[option] = true
		lastOption = true
	}
	return sections, defaults, true
}

func utf8First(s string) (rune, int) {
	for _, r := range s {
		return r, len(string(r))
	}
	return 0, 0
}

// autodetectAWSProfile mirrors the body of get_aws_profile's try block: the
// first `[profile <name>]` section carrying a role_arn or credential_source
// option (its own or DEFAULT's) wins; anything unparseable means nil.
func (c *Config) autodetectAWSProfile() *string {
	path := home.Join(c.homeDir, ".aws", "config")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	sections, defaults, ok := parseAWSConfig(string(data))
	if !ok {
		return nil
	}
	hasOption := func(s *iniSection, option string) bool {
		return s.options[option] || defaults[option]
	}
	for _, section := range sections {
		if !strings.HasPrefix(section.name, "profile ") {
			continue
		}
		if hasOption(section, "role_arn") || hasOption(section, "credential_source") {
			name := pysem.Strip(strings.TrimPrefix(section.name, "profile "))
			return &name
		}
	}
	return nil
}
