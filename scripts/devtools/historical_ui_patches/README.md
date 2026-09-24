These scripts are preserved as historical one-off patch tools that were used to
rewrite frontend renderer code during rapid UI iteration.

Why they are kept:
- they document earlier renderer experiments
- they can help recover or compare discarded UI variants
- they are useful references when replaying manual patch logic

Why they are isolated here:
- they are not production code
- they directly rewrite source files with ad hoc text replacement
- they are unsafe to run blindly after surrounding files evolve

Usage notes:
- read the script before running it
- confirm the target file still matches the expected structure
- prefer `apply_patch` or normal source edits for new work

Files:
- `patch_remove_unused.py`: removes an unused renderer helper from `renderers.tsx`
- `patch_renderers.py`: rewrites alpha and whale renderers with an early inline-style version
- `patch_ui.py`: rewrites alpha and whale renderers toward the PolyWorld card style
- `rewrite_alpha.py`: standalone rewrite focused on `alphaSignalList`
- `rewrite_whale.py`: standalone rewrite focused on `whaleTrackerList`
