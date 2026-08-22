# Contributing

Keep contributions focused on the authorized reference-video rebuild workflow. Do not submit private videos, likenesses, music, product images, platform captures, model weights, or credentials.

Before opening a pull request:

1. Preserve the provider-neutral Template IR and adapter boundaries.
2. Add or update deterministic tests for schema, state, or CLI changes.
3. Run `python -m unittest discover -s tests -v`.
4. Validate the Skill metadata and every example manifest.
5. Document third-party code, model, checkpoint, binary, font, and dataset licenses separately.
6. Avoid dependencies restricted to research or non-commercial use in the default production path.
7. Use synthetic or clearly redistributable fixtures only.

Changes that expand the supported video class must add positive and negative benchmark cases and must not weaken conservative S1–S4 classification behavior.
