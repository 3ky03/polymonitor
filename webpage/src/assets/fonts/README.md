# Bundled interface font

Polymonitor bundles DejaVu Sans Mono 2.37 so the English interface uses the
same glyphs on macOS, Windows and Linux instead of selecting a different
system font on each platform.

The two WOFF2 files contain the complete Regular and Bold font character sets.
They were produced from the upstream DejaVu Sans Mono TTF files with
FontTools `pyftsubset`, WOFF2 compression, all Unicode characters and all
layout features retained.

The font license is included in `DEJAVU-LICENSE.txt`.
