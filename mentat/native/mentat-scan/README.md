# Mentat native workbook scanner

Rust accelerates projected XLSX decoding for the existing Graph receipt and
downstream-activity readers. Classification, signed accounting, scope gates,
contradiction handling, provenance and reconciliation remain in Python/R.
This is not a replacement report engine or an outlier filter.

## Build

From `mentat/`, run `powershell -NoProfile -File tools/build_native.ps1 -Test`.
The script uses installed Cargo or the optional project-local toolchain at
`.cache/rust-tools`. It builds the locked dependencies into `.cache/native-target`.
No compiler or downloader runs during an HTTP request. A compiler/toolchain is
needed only to build; the release executable has no Python dependencies.

On another Rust-equipped platform, use:

```
cargo build --release --locked --manifest-path native/mentat-scan/Cargo.toml --target-dir .cache/native-target
```

`app/native_scan.py` starts at most two hidden, persistent workers. If the binary
is absent, older than its source files, disabled with `MENTAT_NATIVE=off`, or both
workers are occupied, the reference Python reader remains available. Explicit
`MENTAT_NATIVE_SCANNER` selects another build. A native parse error fails the read;
it never becomes an empty or zero-valued dataset. Workers time out after 120 seconds.

## Protocol and scope

One JSON request per stdin line, one JSON response per stdout line, protocol 1:

```
{"path":"absolute workbook path","tables":[{"name":"Downstream Activity","header":5,"columns":["cycle","net_included_amount"]}]}
```

Responses contain sheet names and projected headers/rows. Scalar values are
unrounded source strings or null. Cached formula results are read, not calculated.
Shared and inline rich strings are supported; phonetic annotations are not data.
No XML document types, external worksheet targets, package extraction, or source
writes are allowed. Limits: 32 requested tables, 1,000,000 rows per table, 16,384
columns, and 2 GB uncompressed XML per entry. Source changes during reading fail
closed. This decoder is for supported Mentat report tables, not arbitrary formatted
spreadsheet/date interpretation.

`python -m unittest discover -s tests -p test_native_scan.py -v` checks reference
parity, signed outliers, missing amounts, source immutability, rich strings,
formula caches, errors, limits, concurrency and duplicate-report rejection.
`python tools/benchmark_native_ui.py` generates only a synthetic workbook in
`.cache/ui-performance-20260920` and requires exact result equality before reporting
timings. Its measurements are local parsing/view timings, not FEC network collection,
Excel report generation, browser frame rate, or a guaranteed end-to-end speedup.

Official references: [Rust installation](https://rust-lang.github.io/rustup/installation/),
[rustc linker options](https://doc.rust-lang.org/rustc/codegen-options/index.html),
[quick-xml Reader](https://docs.rs/quick-xml/0.31.0/quick_xml/reader/struct.Reader.html).
