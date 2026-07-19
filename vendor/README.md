# Vendored source databases

These are snapshots of the upstream open-source databases that `models.py` is
built from. They are vendored here so the build is fully reproducible offline
and survives the upstream URLs disappearing.

Snapshot taken: **2026-07-19**

| File | Upstream | License |
|------|----------|---------|
| `epson_print_conf.py` | https://github.com/Ircama/epson_print_conf | **EUPL-1.2** — see `epson_print_conf.LICENSE` |
| `epson.toml` | https://codeberg.org/atufi/reinkpy | credited; see project for terms |
| `epson_print_conf.LICENSE` | epson_print_conf's LICENSE file | EUPL-1.2 text |

## Refreshing

To pull fresh copies from upstream and rebuild:

```
python tools/build_models.py --refresh
```

That overwrites the two data files above and regenerates `../models.py`.
Without `--refresh`, the build reads these vendored copies only (no network).

## Note on EUPL-1.2

`epson_print_conf.py` is copyleft (EUPL-1.2). Redistributing it — and the
`models.py` derived from it — carries EUPL-1.2 obligations, which is why the
project recommends releasing under EUPL-1.2 or a compatible licence. The full
licence text is included alongside the file, as EUPL-1.2 requires.
