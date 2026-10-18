# LibreOffice in the Melange desktop application

The Melange desktop package includes LibreOffice. The application uses it only for local, headless workbook recalculation.

## What is included

- **Product:** LibreOffice 26.8.1 for Windows x64 (build 26.8.1.1, `LibreOffice 26.8.1.1 bf6ef2662401c7d6dce671ab4deb26ee0af3a7c1`).
- **Location in the package:** `resources/runtime/melange/native/LibreOffice/`
- **Unmodified official binaries:** the files come from The Document Foundation's official installer, `LibreOffice_26.8.1_Win_x86-64.msi`:
  - 374,870,016 bytes, SHA-256 `c6298b10cfa1748bcbedad94c62ce771e240e5021bfd6d7a967cd571b04b5312`, Authenticode signature by The Document Foundation
  - extracted with the Windows Installer administrative-image mode (`msiexec /a`)
  - nothing is recompiled, patched or replaced
  - the packaging removes only the copy of the `.msi` that the administrative image keeps, and adds one file, `provenance.json`, that records where the files came from.
- **Download location of that installer:** <https://download.documentfoundation.org/libreoffice/stable/26.8.1/win/x86_64/LibreOffice_26.8.1_Win_x86-64.msi>. The archive copy, with identical SHA-256, is at <https://downloadarchive.documentfoundation.org/libreoffice/old/26.8.1.1/win/x86_64/LibreOffice_26.8.1.1_Win_x86-64.msi>.

## License

LibreOffice is made available under the Mozilla Public License, version 2.0 (MPL-2.0): <https://www.mozilla.org/en-US/MPL/2.0/>. The complete license text and all third-party terms ship unchanged inside the package:

- `native/LibreOffice/license.txt` and `native/LibreOffice/LICENSE.html`: the MPL-2.0 text and the "Third Party Code" section, which gives additional copyright notices and license terms for components.
- `native/LibreOffice/NOTICE`: notices required by the Apache License 2.0 for Apache-licensed portions (Apache OpenOffice, Apache Lucene, Redland, Apache BeanShell). Apache License 2.0 section 4(d) requires recipients to receive these, so the file is kept byte-for-byte.
- `native/LibreOffice/CREDITS.fodt` and `native/LibreOffice/readmes/`: upstream credits and read-me files, kept unchanged.

### Component licenses

`license.txt` lists component terms by group: 97 libraries, 1 extension, 19 fonts, 64 dictionaries and 6 artwork sets.
- Components carry their own terms. These include LGPL-2.1/3, GPL-2/3, MPL-1.1/2.0, Apache-2.0, CPL-1.0, SIL OFL-1.1, LPPL and CC BY-SA 3.0/4.0, among others listed there.
- Some components offer alternatives rather than requiring every listed license. Two examples:
  - **librevenge** (`program/revenge.dll`): "LGPL v2.1+ / MPL 2".
  - **Raptor RDF Parser Library** (`program/raptor2.dll`): LGPL 2.1 or Apache License 2.0. The entry states that these are alternatives and only the selected one applies.
- The package does not itself select or relicense anything. The applicable terms for each component are those stated in `license.txt`.

## Corresponding source code

The Document Foundation publishes the source code for this exact version at <https://download.documentfoundation.org/libreoffice/src/26.8.1/>:

| Archive | Bytes | SHA-256 (from the published `.sha256`) | Signature |
|---|---|---|---|
| `libreoffice-26.8.1.1.tar.xz` (core) | 302,435,508 | `296ba78e7ab0d381b94c3d6342f4ebb2320ee5097ca58781b9b744cab90b9785` | `libreoffice-26.8.1.1.tar.xz.asc` |
| `libreoffice-dictionaries-26.8.1.1.tar.xz` | 62,319,860 | `ce366c50178f5bdf6df1c6df4a0146899cf27a0017734190d00d90f0c5acac61` | `libreoffice-dictionaries-26.8.1.1.tar.xz.asc` |
| `libreoffice-help-26.8.1.1.tar.xz` | 53,513,324 | `f1de4054194e8718f699ddf2b269165613c8dd07c10c55b32968c977f0c6234f` | `libreoffice-help-26.8.1.1.tar.xz.asc` |
| `libreoffice-translations-26.8.1.1.tar.xz` | 236,160,656 | `789519befa409d7eec24043cea41ddd318d9f1212f555d8b28d4982e35693bfc` | `libreoffice-translations-26.8.1.1.tar.xz.asc` |

The four archives above are LibreOffice's own sources. The third-party libraries it builds against, such as librevenge and raptor2, are not inside the core archive. The core archive's build fetches them, as listed in its `download.lst`, from The Document Foundation's external-source mirror at <https://dev-www.libreoffice.org/src/>. That mirror was reachable on 2026-10-16 and lists librevenge and raptor2 tarballs. The exact per-component version mapping is defined by `download.lst` in the core archive. This document does not reproduce that mapping.

Values recorded 2026-10-16 from the official TDF servers.
