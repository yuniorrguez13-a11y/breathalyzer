# Making a release zip

`tools/make_release.py` makes one Breathalyzer zip that is safe to share.
It holds our tools, native sources, examples, docs and tests. Never game files.

```
python tools/make_release.py --dry-run                              # list what would go in; write nothing
python tools/make_release.py --version 0.1.0-dev                    # write the zip
python tools/make_release.py --version 0.1.0-dev --with-binaries    # also the DLLs we built
```

The zip lands in `out\release\breathalyzer-<version>.zip`. `out\` is ignored by git.
Without `--version`, the version is the `VERSION` line at the top of the tool.
`--out` can name another folder: one outside the project, or one under `out\`.
A folder inside the project is refused, because the next release would pack this one.

Exit codes:

- 0: written (or, with `--dry-run`, it would be).
- 1: refused. Every reason is listed.
- 2: bad arguments (a bad version, or an `--out` inside the project).
- 3: the files couldn't be written. For example, the old zip is open in another program, or the disk is full.
  The message says what is left. No `.tmp` file stays behind.

## What goes in

Only an allow-list:

- `tools/`, `native/`, `docs/`, `tests/`, `.github/`, `assets/` (the app icon)
- `README.md`, `LICENSE`, `NOTICE.md`, `.gitignore`, `.cleanallow`
- any other file git tracks

Inside those, `.gitignore` still counts: what git ignores stays out.
So git is needed. If git is missing or fails (for example "dubious ownership"), the tool refuses
and shows git's message.
These never go in, whatever git says, in any letter case: `out/`, `build/`, `upstream/`, `__pycache__`, `.pyc` files.

Everything sits in one folder in the zip, `breathalyzer-<version>/`.

## Checks before writing

Every file must pass all of these. If one file fails, nothing is written, and each failing file is listed.

- All of `tools/check_clean.py`'s rules (the tool imports them): game file types, game file headers, keys,
  saves, generated game code, logs, screenshots, files over 10 MB. A path in `.cleanallow` skips only these rules.
- No leaks, in any file or file name (text or binary, UTF-8 or UTF-16, as a whole word):
  - your Windows user name and your home folder's name (very common ones such as `runner` are skipped as words);
  - your home folder's full path, in every spelling: `C:\Users\you`, `C:/Users/you`, `C:\\Users\\you`,
    `/c/Users/you` and the short 8.3 form (`C:\Users\ALEXAN~1`). This works for short or common names too;
  - the repo's own path;
  - your git e-mail and git name. The git name is fine when `LICENSE` holds it.
- No secrets: no private keys and no access tokens (GitHub, AWS, Google, Slack, Stripe, npm, PyPI, `sk-` API keys).
  No key files either: `.env`, `id_rsa`, `id_ed25519`, `.pem`, `.key`, `.pfx`, `.p12` and the like.
- Archives: zip and gzip files are opened, and every file in them gets these same checks.
  An archive inside one is refused. Other kinds (7z, rar, xz, tar, ...) are refused: what's inside can't be checked.
  A zip in `.cleanallow` skips check_clean's rules for its files too, but never the leak and secret checks.
- No links or junctions, also not in a folder above the file: a link could point at the game.
- No earlier release: a `breathalyzer-*.zip` or `*.SHA256SUMS.txt` is never packed again (nor one from before
  the rename, `botw-mod-loader-*`).
- No file over 20 MB.

## Checks after writing

The tool opens the zip again and checks every entry:

- Names: no absolute paths, no `..`, no backslashes, all inside the one folder, nothing that wasn't planned,
  and no user name in a name.
- Content: the same SHA-256 as the file that was checked, `check_clean`'s rules again (on the files as they come
  out of the zip), the leak, secret and archive checks again, no file over 20 MB, and `SHA256SUMS.txt` matches
  the files.

If any check fails, the zip is deleted and the reasons are listed.

## The DLLs (--with-binaries)

`--with-binaries` adds the DLLs `tools/build_native.py build` made, if they exist:
`modhost.dll` and the example mods (`hello.dll`, `stamina_hook.dll`, `ghost.dll`).
DLLs that aren't built are left out, and the tool says which.
They keep their place in the zip, `build/native/...`, so the mod manager and `build_native.py install` find them.

Each DLL must be:

- a 64-bit Windows DLL (the tool reads its export table);
- one of ours: the example mods export `swmod_init`; `modhost.dll` exports `swgame_tables` and holds modhost's
  own log text;
- inside `build\native`, with no link on the way, and not from the Savage Wind folder;
- not a hard link to a file in the Savage Wind folder (on Windows the tool lists the file's other names;
  elsewhere any hard link is refused);
- never `swgame.dll` (the game's code), and no exported game functions (`f_<address>`).

The self-test DLLs in `build\native\selftest` never go in.

## The SHA-256 lists

- Inside the zip: `breathalyzer-<version>/SHA256SUMS.txt`, one line per file (UTF-8).
- Next to the zip: `breathalyzer-<version>.SHA256SUMS.txt`, the same lines plus the zip's own hash.

To check a download in PowerShell:

```
Get-FileHash breathalyzer-0.1.0-dev.zip -Algorithm SHA256
```

## Same input, same zip

With the same Python version, the same files give a byte-identical zip: names are sorted, every date is
1980-01-01, every file mode is the same. A file's date on disk doesn't matter, only its content.
Another Python version can give other bytes: Python's zlib does the compressing, and Python 3.14 on Windows
uses zlib-ng. Make releases you want to compare with the same `python`.
