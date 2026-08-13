# scrum-agent → yeaboi

**This package was renamed to [`yeaboi`](https://pypi.org/project/yeaboi/).**

`scrum-agent` is now a thin redirect that just depends on `yeaboi`, so any
existing install keeps working:

```bash
pip install scrum-agent      # installs yeaboi
uv tool install scrum-agent  # installs yeaboi (exposes the `yeaboi` command)
```

New installs should use the new name directly:

```bash
uv tool install yeaboi
yeaboi --setup
yeaboi
```

- Homepage: https://yeaboi.ai
- Source: https://github.com/dinho149/yeaboi.ai
