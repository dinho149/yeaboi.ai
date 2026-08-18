package config

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/home"
)

func mapEnv(m map[string]string) home.Env {
	return func(key string) (string, bool) {
		v, ok := m[key]
		return v, ok
	}
}

func loadIn(t *testing.T, env map[string]string, files map[string]string) *Config {
	t.Helper()
	tmp := t.TempDir()
	homeDir := filepath.Join(tmp, "home")
	if err := os.MkdirAll(homeDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for rel, content := range files {
		path := filepath.Join(tmp, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	full := map[string]string{"HOME": homeDir}
	for k, v := range env {
		full[k] = v
	}
	c, err := Load(mapEnv(full), tmp, filepath.Join(homeDir, ".yeaboi", ".env"))
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestLoadLayering(t *testing.T) {
	c := loadIn(t,
		map[string]string{"JIRA_EMAIL": "env@x"},
		map[string]string{
			".env":              "JIRA_EMAIL=proj@x\nLLM_MODEL=proj-model\n",
			"home/.yeaboi/.env": "LLM_MODEL=user-model\nJIRA_BASE_URL=https://user.example\n",
		},
	)
	if got := c.GetJiraEmail(); got == nil || *got != "env@x" {
		t.Errorf("env should beat both .env layers, got %v", got)
	}
	if got := c.GetLLMModel(); got == nil || *got != "proj-model" {
		t.Errorf("project .env should beat user .env, got %v", got)
	}
	if got := c.GetJiraBaseURL(); got == nil || *got != "https://user.example" {
		t.Errorf("user .env should fill unset keys, got %v", got)
	}
}

func TestLoadRespectsDotenvKillSwitch(t *testing.T) {
	c := loadIn(t,
		map[string]string{"PYTHON_DOTENV_DISABLED": "1"},
		map[string]string{".env": "LLM_MODEL=should-not-load\n"},
	)
	if got := c.GetLLMModel(); got != nil {
		t.Errorf("PYTHON_DOTENV_DISABLED=1 must skip .env loads, got %v", *got)
	}
}

func TestParseAWSConfigErrors(t *testing.T) {
	bad := map[string]string{
		"duplicate section":  "[profile a]\nx=1\n[profile a]\ny=2\n",
		"duplicate option":   "[profile a]\nrole_arn=1\nrole_arn=2\n",
		"before header":      "region = x\n[profile a]\nrole_arn=1\n",
		"undelimited line":   "[profile a]\nbadline\n",
		"orphan indentation": "[profile a]\n  dangling\n",
		"unclosed header":    "[profile a\nrole_arn=1\n",
	}
	for name, text := range bad {
		if _, _, ok := parseAWSConfig(text); ok {
			t.Errorf("%s: parseAWSConfig accepted what configparser rejects", name)
		}
	}
	good := map[string]string{
		"continuation":       "[profile a]\nrole_arn = arn\n  continued\n",
		"blank inside value": "[profile a]\nrole_arn = arn\n\n  continued\n",
		"comments":           "; c\n# c\n[profile a]\nrole_arn=1\n",
		"colon delimiter":    "[profile a]\nRole_ARN : 1\n",
		"junk after bracket": "[profile a] trailing\nrole_arn=1\n",
		"crlf line endings":  "[profile a]\r\nrole_arn=1\r\n",
		"repeated [DEFAULT]": "[DEFAULT]\nx=1\n[profile a]\nrole_arn=1\n",
	}
	for name, text := range good {
		if _, _, ok := parseAWSConfig(text); !ok {
			t.Errorf("%s: parseAWSConfig rejected what configparser accepts", name)
		}
	}
}

func TestAutodetectAWSProfile(t *testing.T) {
	c := loadIn(t, nil, map[string]string{
		"home/.aws/config": "[DEFAULT]\ncredential_source = Environment\n\n[default]\nregion=us\n\n[profile via-default]\nregion=eu\n",
	})
	// The DEFAULT section's options count for has_option, so the first
	// `[profile ...]` section matches even without its own role_arn.
	got := c.GetAWSProfile()
	if got == nil || *got != "via-default" {
		t.Errorf("GetAWSProfile = %v, want via-default", got)
	}
}

func TestAWSProfileEnvWins(t *testing.T) {
	c := loadIn(t, map[string]string{"AWS_PROFILE": "explicit"}, map[string]string{
		"home/.aws/config": "[profile assumed]\nrole_arn = arn\n",
	})
	if got := c.GetAWSProfile(); got == nil || *got != "explicit" {
		t.Errorf("GetAWSProfile = %v, want explicit", got)
	}
}

func TestSetConfigValueHardens(t *testing.T) {
	c := loadIn(t, nil, nil)
	path, err := c.SetConfigValue("TIPS_ENABLED", "false")
	if err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Errorf(".env mode = %o, want 600", fi.Mode().Perm())
	}
	dir, err := os.Stat(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	if dir.Mode().Perm() != 0o700 {
		t.Errorf("config dir mode = %o, want 700", dir.Mode().Perm())
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "TIPS_ENABLED='false'\n" {
		t.Errorf("content = %q", data)
	}
}
