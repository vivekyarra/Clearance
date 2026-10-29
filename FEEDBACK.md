# Feedback for Nebius and NVIDIA

Running log kept while building Clearance for the Nebius x NVIDIA Global AI Hackathon. Each entry says what worked well and what would make it even better, with the page we were on. Newest entries at the top of each day.

Products covered: Nebius Token Factory, Nebius AI Cloud (Serverless AI, Object Storage, Container Registry, CLI), NVIDIA Nemotron and Cosmos models, plus Tavily (part of the Nebius portfolio).

## Scores

| Product | Score | One line |
|---|:-:|---|
| Nebius Token Factory | 9/10 | 90 live calls, 0 failures. Fast, stable, OpenAI-compatible. Pricing and model lifecycle cost us time. |
| NVIDIA Nemotron (3.5 Super VL, 3 Ultra, 3.5 Lightning) | 9/10 | Super VL runs our investigation loop well. Ultra writes the best reports we tested, but far too long. |
| Tavily | 9/10 | Search + Extract gave us specs we can quote word for word. Ranking needs an exact-model option. |
| Nebius AI Cloud (Serverless, Object Storage, CLI) | 8/10 | Excellent docs and CLI. Getting a GPU for a hackathon project means real money on a personal card. |
| NVIDIA Cosmos Reason 2 | 8/10 | The right model for scene hazards. Too hard to reach for a few calls per scan. |

## The hard parts, plainly

These cost us the most time or forced a change of plan. All are from this build.

1. **A GPU means a personal card charge.** The Builder Program gives $25 of Token Factory credit, and that covers model calls only. Cosmos Reason 2 and Serverless Jobs need Nebius AI Cloud, which bills the card on file. As a solo builder we could not risk an open-ended charge, so our Cosmos endpoint and simulation jobs ship as deploy scripts instead of running live. **Ask:** hackathon AI Cloud credits, or a hard spend cap that refuses to charge the card once credits run out.
2. **Cosmos Reason 2 is not on Token Factory.** Using it means a dedicated GPU endpoint, a Hugging Face token, a vLLM image and a preset choice, for a model we call a few times per scan. We ran scene hazards on Nemotron 3.5 Super VL instead. **Ask:** Cosmos Reason 2 as a serverless Token Factory model, billed per token.
3. **The models we built on retire during judging.** Nemotron 3 Nano 30B and Nemotron 3 Super 120B leave Serverless on 2026-11-09. Submissions close 2026-10-30 and judging runs 2026-12-01 to 12-15. We migrated to the 3.5 models mid-build. **Ask:** keep event-recommended models live until judging ends, or offer stable aliases such as `nemotron-super-latest`.
4. **Prices are behind a sign-in.** The prices page renders only in a signed-in browser, so our scripts and coding agents saw an empty page and we had to mark our cost table as a list-price estimate. **Ask:** a public, static price table, and a link to the `pricing` object in `GET /v1/models?verbose=true`.
5. **Four model ID styles.** `NVIDIA-Nemotron-3-Nano-30B-A3B`, `nemotron-3-super-120b-a12b`, `Nemotron-3_5-Lightning`, `Nemotron-3.5-Super-VL`. Each style difference was a "model not found" until we normalised IDs in our own script. **Ask:** one naming convention, or case- and punctuation-insensitive matching.
6. **Ultra writes ten times what we ask for.** About 2,500 output tokens per call for sections budgeted at 120–250 words. Once, a regenerated section came back as the model's working notes instead of prose, and our fact checker had to cut it apart. We now replace any section over twice its budget with a code template. **Ask:** a per-request switch for reasoning length on Ultra, and reasoning tokens reported separately in `usage`.
7. **The API key arrived on day 2.** We planned and built day 1 without a key, from the docs alone. **Ask:** issue keys (even rate-limited ones) at registration.
8. **Credits arrived the day before the deadline.** The Builder Program welcome email came on 2026-10-29, with the Token Factory credit "in a separate email". **Ask:** send credits at kickoff so teams can plan their spend.

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
