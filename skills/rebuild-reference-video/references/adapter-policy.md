# Processor and provider adapter policy

## Selection order

Choose the least generative option that can satisfy the template:

1. direct use;
2. crop, scale, color, mask, and deterministic composite;
3. static controlled image generation or virtual try-on;
4. tracked composite with generated stills;
5. short video modification or generation;
6. manual fallback or unsupported classification.

## Adapter declaration

Every adapter must declare:

- identifier and version;
- accepted inputs and produced outputs;
- local, cloud, or remote-worker execution;
- license for code and weights;
- commercial-use status;
- hardware and memory requirements;
- expected runtime and separate provider cost;
- whether seeds and versions are reproducible;
- upload, retention, and deletion behavior;
- content and identity limitations;
- emitted provenance fields;
- retry policy and stable error codes.

## Local-only profile

Do not make network calls. Verify that required weights are present and licensed. Stop with a capability error if hardware is insufficient; do not silently enable cloud processing.

## Cloud-assisted profile

Require explicit per-project authorization and name the provider. Upload only the minimum approved assets. Do not send the full source video when a derived frame or mask is sufficient. Record provider request IDs and retention policy without logging credentials.

## License rules

- Treat repository code, model weights, training data, and runtime binaries as separate license surfaces.
- Do not ship non-commercial adapters as production defaults.
- Do not copy from repositories without a license.
- Keep third-party attribution with redistributed compatible code.
- Pin reviewed versions and re-run license checks on upgrades.

## Quality routing

Prefer slot-specific retry over whole-video regeneration. Freeze an approved identity asset and use it consistently. If a provider cannot preserve required product text, logo, silhouette, or identity, mark the adapter unsuitable for that slot.
