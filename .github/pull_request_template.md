## What / why

## Vanilla reference

<!--
linuxdoom-1.10 file/function, or Chocolate Doom behavior observed.
Skip only for pure tooling with no vanilla counterpart.
-->

## Verification

- [ ] `python -m pytest tests/` green (276 tests)
- [ ] headless smoke: `tools/doom_view.py --frames=…` on touched maps
- [ ] demo checksum unchanged where the sim is touched
      (`--record` vs `--play` must agree)

## Notes

<!--
Engine-behavior changes need a NOTE: comment at the point of
divergence, and docs/DIVERGENCES.md updated when the behavior is
deliberately non-vanilla.
-->
