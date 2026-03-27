# Vendored stock PDF.js generic viewer

This directory is reserved for upstream PDF.js generic viewer assets copied into package scope.

Required minimum files:

- `web/viewer.html`
- `web/viewer.js`
- `build/pdf.mjs`
- `build/pdf.worker.mjs`

Copy all additional upstream assets referenced by `viewer.html` (for example `viewer.css`, locale/cmaps/images) so the viewer works offline.

License: Apache-2.0 (Mozilla PDF.js). Keep upstream LICENSE/NOTICE files with the vendored copy.
