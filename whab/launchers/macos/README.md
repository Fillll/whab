# WHAB for macOS

WHAB.app is a native companion that installs and runs WHAB on your Mac. It opens
the budgeting interface in your browser and keeps Start, Stop, Restart, updates
and settings in the menu bar. The launcher has its own version, recorded in
`VERSION`; the installed WHAB host and each plugin keep their existing versions.

Requires macOS 13 or newer. Build on the architecture of the Mac that will run
the app; the initial local build targets Apple silicon. Public Intel and universal
artifacts require matching Python runtimes and separate build validation.

## First launch

1. Open WHAB.app. Select **Use default runtime** for a new private environment,
   or **Choose…** to adopt the folder containing your existing WHAB `.env`, `data`,
   `budgets` and `tls`. Stop any manually launched WHAB server before adopting it.
2. Choose **Latest tested main** or **Stable releases**, then **Start WHAB**.
   Start prepares the newest verified version when available, starts the local
   HTTPS server and opens your browser. Repeated Open clicks reuse that server.
3. For a new runtime, use **HTTPS certificate…** to open its CA certificate in
   Keychain Access. Add the certificate, open it, expand **Trust**, and explicitly
   trust it for SSL. macOS may request your password. The launcher never changes
   system trust itself.

The default runtime is `~/Library/Application Support/WHAB/runtime`. Launcher
configuration, installed versions, private backups and logs live separately in
`~/Library/Application Support/WHAB/launcher`. Choosing an existing runtime preserves
its settings and encryption keys; neither setup nor updates create bank connections.
Treat the whole runtime and its backups as private financial data.

**Restart** restarts the installed version. **Update & Restart** prepares and tests
an update, stops the running version, creates a consistent private runtime backup,
then switches versions. A running budget is never interrupted by a background
update check. **Check for updates** records availability; **Start** also checks.
When offline, the last working installation remains usable. A first installation
can use the app's bundled source and wheelhouse without network access.

Latest tested main means the newest official `main` revision with successful CI.
Stable means a supported official GitHub release. A locally built source seed is
labelled development when the checkout is dirty and is not represented as CI tested.
The launcher rejects unsupported old layouts instead of silently installing an
incompatible release. Changing the runtime or update channel requires stopping WHAB.

Closing the window leaves the launcher in the menu bar. **Quit launcher** leaves
the budget server running; use **Stop** to shut down the budget. Optional **Open
launcher at login** uses macOS Login Items. After your initial explicit runtime
setup, opening WHAB.app starts the budget (or reuses its running server) and opens
the browser. Reopening it from Spotlight or the Dock does the same.

## Build a complete local app

From `whab/app/`, use the development environment backed by a clean relocatable
Python 3.14 distribution, such as python-build-standalone:

```sh
.venv/bin/python ../launchers/macos/scripts/build.py --python .venv/bin/python
```

The default output is `whab/launchers/macos/dist/WHAB.app`. Copy it to your user
Applications folder or `/Applications`, then open it from Finder or Spotlight.
Use `--output /absolute/path/WHAB.app` to choose another output, and `--replace`
to replace an existing output after a successful build. Building downloads
dependency wheels; the resulting app does not require your checkout, a terminal,
Git, a separately installed Python, or network access for its initial bundled version.

The builder compiles the native app, copies only a source allowlist, bundles a
private Python runtime, builds the host and seven maintained plugin wheels plus
dependencies, verifies Python after relocation, and signs the local bundle ad hoc.
It rejects framework, system and Homebrew Python installations. Virtualenvs are
accepted only when their underlying interpreter is suitable. Developer `.venv`
packages, runtime `.env`, ledgers, keys, TLS material and bank credentials are
excluded. Only the committed setup template `.env.example` is included.

This is a local development build. Ad-hoc signing is not Developer ID signing or
Apple notarization; public distribution needs that separate packaging work. This
command does not create a Git tag, publish a release, or install into Applications.

## Development and diagnostics

Run native protocol and path-safety tests from the repository root:

```sh
CLANG_MODULE_CACHE_PATH=/tmp/whab-clang-cache \
SWIFT_MODULECACHE_PATH=/tmp/whab-swift-cache \
swift test --package-path whab/launchers/macos \
  --scratch-path /tmp/whab-launcher-build --disable-sandbox
```

Build only the native shell for CI without copying Python or downloading wheels:

```sh
python3 whab/launchers/macos/scripts/build.py --skip-runtime --output /tmp/WHAB.app
```

A shell-only app cannot run WHAB. A complete app supports JSON diagnostics and
an isolated launcher root for demo testing:

```sh
whab/launchers/macos/dist/WHAB.app/Contents/MacOS/WHABLauncher \
  --diagnose --root /tmp/whab-launcher-demo/launcher
```

Verify a complete bundle's offline lifecycle with the supported development
Python 3.14 interpreter, from `whab/app/`:

```sh
.venv/bin/python ../launchers/macos/scripts/check_bundle.py \
  --bundle ../launchers/macos/dist/WHAB.app
```

This check uses the bundle's own Python, manager, server, source seed and wheels.
It creates a temporary runtime and fictional manual account, verifies the rendered
demo page and HTTPS build identity, checks restart/relaunch persistence, exercises
native diagnostics, and verifies private state/backup permissions and the final
app signature. It never uses your existing runtime or modifies certificate trust.
It always attempts a graceful stop before cleanup. Add `--keep-test-dir` to retain
its disposable files and private logs; a failed stop also retains them.

The same `--root` flag works when opening the native app executable normally.
Add `--no-auto-start` when testing the UI to show settings without starting or
opening a configured budget. Periodic status checks never start a server.
Diagnostics inspect the chosen launcher and may create its private state/lock
directory. They never start a server or change certificate trust. Do not use your
personal runtime for automated or browser tests. The stdlib Python manager's JSON
CLI is documented in [manager/README.md](manager/README.md).

Manager processes run asynchronously with separate argument arrays; no shell
interprets user paths. URLs opened from manager responses must be HTTPS on a
loopback host. The app shows short status/errors, and **View logs** opens the
private local log file without uploading its contents.
