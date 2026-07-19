# Attribution

`models.py` in this project is a derived database. The reverse-engineered model
constants it contains — the read/write keys and the EEPROM waste-counter reset
addresses for each printer — were produced by the following open-source projects.
This project would not exist without their work.

## Ircama/epson_print_conf

- Source: https://github.com/Ircama/epson_print_conf
- License: **EUPL-1.2** (European Union Public Licence 1.2)
- Copyright (c) 2024–2025 Ircama
- Contribution here: the verified model profiles (107 models) with counter
  dividers, and the OID / EEPROM protocol details this tool's encoder implements.

Because EUPL-1.2 is a copyleft licence, redistributing the derived `models.py`
carries EUPL-1.2 obligations. Releasing this repository under EUPL-1.2 (or a
compatible licence) keeps it in compliance.

## atufi/reinkpy

- Source: https://codeberg.org/atufi/reinkpy
- A free and open waste-ink counter resetter for Epson printers.
- Contribution here: the large model-coverage database (`epson.toml`) that
  extends support to ~1,268 additional models.

## What this project adds

- A dependency-free, single-file reader/resetter (`reset_epson.py`) with a
  hand-rolled SNMPv1 encoder and a Windows-spooler USB transport.
- A merge/cross-validation build step (`tools/build_models.py`) that reconciles
  the two databases and verifies they agree on every overlapping model.

No model data here originates from any commercial/proprietary reset utility.
