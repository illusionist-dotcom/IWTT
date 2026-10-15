# IWTT v0.1 Privacy

## Local storage

The default vault is `~/.iwtt/thought-vault.sqlite3`. Set `IWTT_DATA_DIR` or use `--data-dir` to choose another directory. SQLite runs on the same machine as the IWTT process. Backups live in `<data-directory>/backups/` and a copy is sent to the browser as a download when requested.

## Network behavior

Basic use requires no internet connection. The local HTTP server binds to `127.0.0.1` by default. The browser talks to that local process; the app contains no telemetry, analytics, advertising, tracking pixels, CDN resources, or external fonts/scripts. No vault content is transmitted by default.

The server has no authentication or multi-user access control. Do not bind it to a LAN or public interface unless you deliberately add access controls and understand the exposure. An `0.0.0.0` bind is used only in some hosted preview environments; it is not the documented local default.

## AI

No provider is configured by default. If `IWTT_AI_BASE_URL` is set, the configured service receives text only when the user explicitly invokes an AI action. A remote endpoint is possible only by deliberate configuration. Read [AI_BOUNDARIES.md](AI_BOUNDARIES.md) before enabling it. Do not include API keys in files or exported vaults.

## User responsibility

The app does not encrypt the SQLite file at rest. Use operating-system disk encryption and secure backups on shared or portable devices. The app cannot verify whether the browser's download location is private. Keep the database and backups somewhere you control.
