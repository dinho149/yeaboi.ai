// Port of src/yeaboi/config.py (environment resolution and the .env write
// path) — keep in lockstep; the Python module is the reference
// implementation and tests/parity/foundations/ diffs every getter's output
// against committed goldens.
//
// Python layers its environment imperatively: `load_dotenv()` at import
// pulls in the project .env, `load_user_config()` at startup pulls in
// ~/.yeaboi/.env, both with override=False so the process environment always
// wins; every getter then reads os.getenv live. Go resolves the same three
// layers once per process (the W8 spec pins this equivalence) into one
// Lookup the getters share. The per-setting setters (set_tips_enabled and
// friends) mirror a live os.environ the frozen Lookup deliberately lacks;
// they arrive with the Settings screen in W17 — this wave ports their one
// choke point, SetConfigValue, which is where the .env write and the 0600
// hardening live.
package config

import (
	"errors"
	"io/fs"
	"os"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/dotenv"
	"github.com/yeaboi-ai/yeaboi/go/internal/home"
)

// Config resolves every config.py getter against one effective environment.
type Config struct {
	env     home.Env // process env + project .env + user .env, layered once
	homeDir string   // Path.home() at resolve time
}

// dotenvDisabled ports _load_dotenv_disabled: presence plus a truthy value
// (casefolded — ASCII here, so ToLower matches) disables both .env loads.
func dotenvDisabled(env home.Env) bool {
	v, ok := env("PYTHON_DOTENV_DISABLED")
	if !ok {
		return false
	}
	switch strings.ToLower(v) {
	case "1", "true", "t", "yes", "y":
		return true
	}
	return false
}

// layer returns a Lookup over base plus the keys a .env load added.
func layer(base home.Env, added map[string]string) home.Env {
	if len(added) == 0 {
		return base
	}
	return func(key string) (string, bool) {
		if v, ok := base(key); ok {
			return v, true
		}
		v, ok := added[key]
		return v, ok
	}
}

// Load mirrors the layering above: cwd is where find_dotenv starts its
// walk-to-root for the project .env (see internal/dotenv's package comment
// for why that is the frozen-Python behaviour), envFile is paths.ENV_FILE
// (~/.yeaboi/.env, pinned to the bootstrap home).
func Load(env home.Env, cwd string, envFile string) (*Config, error) {
	homeDir, err := home.HomeDir(env)
	if err != nil {
		return nil, err
	}
	effective := env
	if !dotenvDisabled(env) {
		if project := dotenv.Find(cwd); project != "" {
			effective = layer(effective, dotenv.LoadInto(project, dotenv.Lookup(effective)))
		}
		effective = layer(effective, dotenv.LoadInto(envFile, dotenv.Lookup(effective)))
	}
	return &Config{env: effective, homeDir: homeDir}, nil
}

// Env exposes the layered effective environment — what os.environ holds
// after Python's two override=False dotenv loads. redaction.py reads its
// secrets from exactly that view, so the logfile surface resolves through
// this rather than the raw process env.
func (c *Config) Env() home.Env {
	return c.env
}

// getenv mirrors os.getenv(key, default): an empty value is a value.
func (c *Config) getenv(key, def string) string {
	if v, ok := c.env(key); ok {
		return v
	}
	return def
}

// getenvOrNil mirrors the `os.getenv(key) or None` idiom.
func (c *Config) getenvOrNil(key string) *string {
	v, ok := c.env(key)
	if !ok || v == "" {
		return nil
	}
	return &v
}

// restrictPermissions ports config.restrict_permissions: best-effort chmod,
// never an error to the caller.
func restrictPermissions(path string, mode fs.FileMode) {
	_ = os.Chmod(path, mode)
}

// GetConfigDir ports get_config_dir: ~/.yeaboi, created on demand and
// re-hardened to 0700 on every call.
func (c *Config) GetConfigDir() (string, error) {
	dir := home.Join(c.homeDir, ".yeaboi")
	if err := os.Mkdir(dir, 0o777); err != nil && !errors.Is(err, fs.ErrExist) {
		return "", err
	}
	restrictPermissions(dir, 0o700)
	return dir, nil
}

// GetConfigFile ports get_config_file: ~/.yeaboi/.env.
func (c *Config) GetConfigFile() (string, error) {
	dir, err := c.GetConfigDir()
	if err != nil {
		return "", err
	}
	return home.Join(dir, ".env"), nil
}

// GetSessionsDB ports get_sessions_db: the legacy ~/.yeaboi/sessions.db,
// re-hardened to 0600 when it exists.
func (c *Config) GetSessionsDB() (string, error) {
	dir, err := c.GetConfigDir()
	if err != nil {
		return "", err
	}
	db := home.Join(dir, "sessions.db")
	if _, err := os.Stat(db); err == nil {
		restrictPermissions(db, 0o600)
	}
	return db, nil
}

// SetConfigValue ports set_config_value, the choke point every setter
// routes through: dotenv set_key semantics, then the credential file is
// re-locked to 0600.
func (c *Config) SetConfigValue(key, value string) (string, error) {
	configFile, err := c.GetConfigFile()
	if err != nil {
		return "", err
	}
	if err := dotenv.SetKey(configFile, key, value); err != nil {
		return "", err
	}
	restrictPermissions(configFile, 0o600)
	return configFile, nil
}
