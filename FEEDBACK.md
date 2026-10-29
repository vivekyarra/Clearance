# Feedback for Nebius and NVIDIA

Running log kept while building Clearance for the Nebius x NVIDIA Global AI Hackathon. Each entry says what worked well and what would make it even better, with the page we were on. Newest entries at the top of each day.

Products covered: Nebius Token Factory, Nebius AI Cloud (Serverless AI, Object Storage, Container Registry, CLI), NVIDIA Nemotron and Cosmos models, plus Tavily (part of the Nebius portfolio).

---

## 2026-10-28/29 — Live runs on Token Factory

Measured on three full Clearance checks: 90 calls (39 Nemotron 3.5 Super VL, 51 Nemotron 3 Ultra), 0 failed, 272,121 tokens in, 146,806 tokens out (`docs/PROOF.md`).

### Token Factory

**L1. Reliability under an agent loop**
- What worked: 90 calls, 0 failures, across hours of tool-calling and long report sections. Median wall time per call was 2.5 s for Super VL and 6.9 s for Ultra, fast enough that the investigation loop feels interactive.
- Even better: a `usage` breakdown of reasoning vs. answer tokens in the response would let us budget Ultra precisely; today one number covers both.

**L2. Ultra writes long**
- What worked: Ultra's narrative quality is the best we tested for the report; it explains trade-offs the way an engineer would.
- Even better: Ultra produced 129,173 output tokens over 51 calls, about 2,500 per call, for sections we budget at 120–250 words. In one regenerated section the answer was the model's working notes instead of prose. A documented way to cap or switch off reasoning per request on Ultra (as some hosts expose with `reasoning_effort` or a thinking toggle) would cut cost and keep answers on format. We now replace any section over twice its word budget with a code template.

**L3. Structured output and tool calling**
- What worked: the OpenAI-compatible tool-calling format worked unchanged with our client, and Super VL picked sensible simulator experiments (in the reference café it moved one chair and success went from 75% to 100%).
- Even better: a per-model table in the docs of what `response_format: json_schema`, `tools` and `tool_choice` support, with a minimal example, would save trial calls during setup.

**L4. Vision on the same endpoint**
- What worked: Nemotron 3.5 Super VL takes keyframes on the same chat endpoint, so scene-hazard checks needed no extra service.
- Even better: Cosmos Reason 2 on Token Factory. Today it needs a dedicated GPU endpoint, which is a large step up in setup and cost for a model called a few times per scan.

**L5. Cost visibility**
- What worked: per-call usage in every response made our cost table exact: about 0.58 USD for three full checks at list price.
- Even better: the prices page needs a signed-in browser; the `pricing` object in `GET /v1/models?verbose=true` is the reliable source, and pointing to it from the prices page would help scripts and agents.

### Tavily

**L6. Search + Extract for verified specs**
- What worked: 30 Search calls and 20 Extract calls (65 URLs) built a 12-robot catalog where every number is backed by a verbatim quote; Extract's clean text made the quote validator simple.
- Even better: ranking sometimes put a sibling model's page ("Servi Plus" for "Servi") above the exact model's own page. An exact-phrase boost option in Search would remove the re-ranking step we added.

---

## 2026-10-27 — Day 1 research (docs and rules)

### Token Factory

**1. Model lifecycle during an event**
- What worked: the deprecation notices are clear, dated and list a recommended replacement for every model, with catalog links (https://docs.tokenfactory.nebius.com/model-deprecations-november-2026.md).
- Even better: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` and `nvidia/nemotron-3-super-120b-a12b` leave Serverless on 2026-11-09, between the submission deadline (2026-10-30) and the judging period (2026-12-01 to 12-15). The hackathon page itself recommends Nemotron 3 Nano / Super / Ultra. A short "hackathon availability" note (or keeping event-recommended models live until judging ends, or a stable alias such as `nvidia/nemotron-super-latest`) would let teams build once and have judges test the same thing.

**2. Consistent model ID style**
- What worked: IDs are visible in docs tables and in `GET /v1/models`.
- Even better: four NVIDIA IDs use four styles: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (prefix, capitals), `nvidia/nemotron-3-super-120b-a12b` (lower case), `nvidia/Nemotron-3_5-Lightning` (underscore for the version), `nvidia/Nemotron-3.5-Super-VL` (dot for the version). One convention, or case/punctuation-insensitive matching on the API, would remove a class of "model not found" errors. We now normalise IDs in our verification script.

**3. A public model catalog page in the docs**
- What worked: `GET /v1/models?verbose=true` returns `pricing`, `context_length` and `supported_features` per model, which is ideal for agents.
- Even better: the pricing page (`tokenfactory.nebius.com/organization/prices`) renders in the browser only, so a script or coding agent sees an empty page, and Nemotron 3 Ultra's exact ID does not appear on any docs page we found (only on third-party catalogs). A static catalog page in the docs (ID, modality, context, price, tool-calling / JSON-schema support) would make Day-1 planning possible before an API key arrives. Adding it to `llms.txt` would help coding agents too.

**4. Rate limits**
- What worked: the dynamic rate-limit page is excellent: rolling 15-minute windows, scale factors, the 20x ceiling and every response header are spelled out with a worked table (https://docs.tokenfactory.nebius.com/ai-models-inference/rate-limits.md).
- Even better: one line on the default base allocation for a new project (or "see console → Rate Limits") next to the example numbers would make clear which numbers are examples.

### Nebius AI Cloud — Serverless AI

**5. CLI reference quality**
- What worked: `nebius ai job create` and `nebius ai endpoint create` are deterministic, documented flag by flag, support `--dry-run` and `--format json`, and the reference page is auto-generated with a date. The `llms.txt` index on docs.nebius.com let us download every page as Markdown. This was the smoothest infra research of the project.
- Even better: a one-line note on allowed job/endpoint name characters and length. Our job IDs are content hashes and we had to choose a conservative name format without a rule to check against.

**6. CPU-only jobs**
- What worked: the docs confirm CPU-only workloads indirectly (20 GiB minimum disk "for CPU-only workloads", vCPU quotas "if you use non-GPU VMs") and the platform page lists `cpu-d3` / `cpu-e2` presets.
- Even better: one explicit CPU job example (`--platform cpu-d3 --preset 4vcpu-16gb`) in the jobs quickstart. Simulation fan-out (the Physical AI track's own example) is mostly CPU work, and every quickstart example is a GPU job.

**7. Environment values that contain commas**
- What worked: `--env` is repeatable, and `--inject-file` covers config files up to 64 KiB.
- Even better: the jobs guide also says several variables can be listed "separated by commas". A sentence on how a value that itself contains a comma (JSON) is handled would help; we base64-encode job specs to stay safe.

**8. WebSocket through the managed endpoint URL**
- What worked: each HTTP port gets a managed `https://` URL, TCP ports get `tls://HOST:443`, and `--auth token` gives a bearer token in one flag.
- Even better: a sentence on whether WebSocket upgrades pass through the managed HTTPS URL. Our live "chaos mode" streams robot state over WebSocket, so this decides between the HTTP port and the TCP port.

**9. Concurrency for fan-out**
- What worked: the quotas page explains exactly which Compute quotas each deployment consumes, with a worked example.
- Even better: a short "running N jobs in parallel" section (how many jobs a new project can typically start at once, and whether there is a submission rate limit on `ai/v1/jobs`).

### Nebius Object Storage

**10. Presigned URLs**
- What worked: S3 compatibility is documented method by method, including `PutBucketCORS`, which browser uploads need.
- Even better: presigned URLs are documented only on the older docs site (nebius.ai/docs) with the older endpoint `storage.ai.nebius.cloud`. A current page with a boto3 `generate_presigned_url` example against `storage.<region>.nebius.cloud` would confirm the path phones and browsers use for uploads.

### NVIDIA models

**11. Cosmos Reason 2 on Nebius**
- What worked: the Cosmos Reason 2 README gives a ready `vllm serve` command, recommended `--max-model-len`, minimum GPU memory per size, and tested GPUs. The Nebius vLLM endpoint tutorial maps onto it directly.
- Even better: a Nebius tutorial (or Serverless template) for Cosmos Reason 2 specifically, naming a validated platform/preset and the Hugging Face token step for the gated 2B/8B weights. Cosmos models are named in the Physical AI track description, and Cosmos Reason 2 is not on Token Factory, so a one-command path would help many teams.

**12. Licenses on model cards**
- What worked: every model card we checked states its license in the card metadata (NVIDIA Nemotron Open Model License, NVIDIA Open Model License, OpenMDW-1.1), which made `THIRD_PARTY_LICENSES.md` quick to build.
- Even better: a short table in NVIDIA's docs of which family uses which license (Nemotron 3 Nano/Super vs. Nemotron 3 Ultra and the 3.5 family vs. Cosmos), and the exact attribution text required ("Built on NVIDIA Cosmos"), in one place.

### Tavily

**13. Docs for agents**
- What worked: rate limits per key type, credit costs per endpoint and depth, and the free 1,000 credits/month are all on two short pages; the `llms.txt` index links every endpoint. Failed extractions are not charged, which suits our catalog builder.
- Even better: a note on hackathon credits, if any, on the Tavily docs or the Devpost page.

### Hackathon rules (for the organizers)

**14. Prize eligibility sentence**
- What worked: the Official Rules are complete and easy to find, with exact Pacific Time deadlines.
- Even better: "Each Project is eligible for one (1) Overall Award OR one (1) Track Award and one (1) Bonus Award" can be read two ways (Overall alone vs. Overall plus a Bonus). A worked example would remove the ambiguity. We are sending the written clarification request the rules describe.
