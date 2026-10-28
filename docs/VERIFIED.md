# Verified facts

Day-1 verification log for Clearance (PLAN Section 0 and Phase 0). Every row records what we found, where we found it, when we checked, and its status:

- **Confirmed from docs**: read in official documentation or the official rules on the date shown.
- **Confirm with account**: not stated in public docs (or stated only by third parties). It must be confirmed with a logged-in account, an API key, or a `--dry-run` before it goes into the README, video or Devpost text.

All dates are 2026-10-27 unless noted. Times in the rules are Pacific Time. Our deadline in IST is shown for convenience.

---

## Action items for the team (read first)

1. **Two Token Factory model IDs we planned around leave Token Factory Serverless on 2026-11-09.** Judging runs 2026-12-01 to 2026-12-15, so the hosted demo must not depend on them. Replacements named by Nebius:
   - `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` → `nvidia/Nemotron-3_5-Lightning`
   - `nvidia/nemotron-3-super-120b-a12b` → `nvidia/Nemotron-3.5-Super-VL`

   Recommendation: develop on the current IDs if they are faster to start with, but set the `active` candidate for `nano` and `super` in `config/models.yaml` to the replacement IDs before submission and test the tool-calling path on them. A Dedicated Endpoint is the other documented option ("Dedicated Endpoints are unaffected").
2. **`nvidia/Nemotron-3-Nano-Omni` and `nvidia/Cosmos3-Super-Reasoner` already left Token Factory on 2026-08-31.** Remove them from any candidate list.
3. **Prize stacking sentence is ambiguous** (see Section 6). Send the written clarification request the rules invite, before the deadline.
4. **Nemotron 3 Ultra's exact Token Factory ID is not in any official page we could read.** Take it from `GET /v1/models` (run `scripts/verify_token_factory.py`).

---

## 1. Nebius Token Factory (public docs)

| # | Finding | Source | Checked | Status |
|---|---|---|---|---|
| 1.1 | Base URL is `https://api.tokenfactory.nebius.com/v1/` (OpenAI-compatible; the official Python/JS examples set this as `base_url`). | https://docs.tokenfactory.nebius.com/api-reference/introduction.md | 2026-10-27 | Confirmed from docs |
| 1.2 | Auth: `Authorization: Bearer <API key>`. Examples read the key from `NEBIUS_API_KEY`. Keys are created at https://tokenfactory.nebius.com/project/api-keys and shown once. | https://docs.tokenfactory.nebius.com/api-reference/introduction.md | 2026-10-27 | Confirmed from docs |
| 1.3 | Chat endpoint `POST /v1/chat/completions`. Model list `GET /v1/models`, optional query params `ai_project_id` and `verbose` (verbose objects add `context_length`, `pricing`, `supported_features`, `regions`, `quantization`). | https://docs.tokenfactory.nebius.com/api-reference/models/list-models.md | 2026-10-27 | Confirmed from docs |
| 1.4 | Public serverless endpoints show region "Global"; processing location can change. A region-pinned host such as `api.tokenfactory.us-central1.nebius.com` exists but can stop working if the region changes, so we use the global base URL. | https://docs.tokenfactory.nebius.com/public-serverless.md | 2026-10-27 | Confirmed from docs |
| 1.5 | Rate limits are dynamic, evaluated in rolling 15-minute windows: x1.2 when average usage is at least 80 % of the limit, /1.5 when at most 50 %, ceiling 20x the base allocation. The docs use 60 RPM / 400,000 TPM only as a worked example; the real base values are shown per project in the console. Exceeding returns HTTP 429 with `Retry-After`; headers `x-ratelimit-limit-requests`, `x-ratelimit-remaining-tokens`, `x-ratelimit-over-limit`, and others. A Batch API has higher limits. | https://docs.tokenfactory.nebius.com/ai-models-inference/rate-limits.md | 2026-10-27 | Confirmed from docs (base values: Confirm with account) |
| 1.6 | API accepts "the full set of vLLM parameters" and most OpenAI generation parameters. A "-fast" flavour exists by appending `-fast` to the model name. JSON-schema / tool-calling support per model is not stated on that page. | https://docs.tokenfactory.nebius.com/ai-models-inference/overview.md | 2026-10-27 | Confirmed from docs (per-model tool/JSON support: Confirm with account via `verbose=true` `supported_features`) |
| 1.7 | Vision requests use OpenAI-style `image_url` content parts (docs example uses `Qwen/Qwen2-VL-72B-Instruct`). | https://docs.tokenfactory.nebius.com/api-reference/examples/vision-capabilities.md | 2026-10-27 | Confirmed from docs |
| 1.8 | Hackathon credits for Token Factory: not stated on the Devpost overview or rules pages. | https://nebiusglobalaihackathon.devpost.com/ | 2026-10-27 | Confirm with account |

### 1A. NVIDIA model IDs on Token Factory

| Model | Token Factory ID | Evidence | Status |
|---|---|---|---|
| Nemotron 3 Nano 30B | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Listed in the Nov 2026 deprecation table with its catalog link. **Leaves Serverless 2026-11-09.** https://docs.tokenfactory.nebius.com/model-deprecations-november-2026.md | Confirmed from docs |
| Nemotron 3 Super 120B | `nvidia/nemotron-3-super-120b-a12b` (lower case) | Code sample on https://nebius.com/services/token-factory/nemotron and the Nov 2026 deprecation table. **Leaves Serverless 2026-11-09.** | Confirmed from docs |
| Nemotron 3.5 Lightning (Nano successor) | `nvidia/Nemotron-3_5-Lightning` (underscore in `3_5`) | Named as the replacement in the Aug and Nov 2026 deprecation tables (text2text catalog). | Confirmed from docs |
| Nemotron 3.5 Super VL (Super successor, vision) | `nvidia/Nemotron-3.5-Super-VL` (dot in `3.5`) | Named as replacement in the Nov 2026 table, catalog category `image2text`. | Confirmed from docs |
| Nemotron 3 Ultra 550B | not found in official pages | The Nebius Nemotron page names "Nemotron 3 Ultra 550b" without an ID. Third-party catalogs disagree (`nvidia/Nemotron-3-Ultra-550b-a55b`, `nvidia/nemotron-3-ultra-550b-a55b`). | Confirm with account (`GET /v1/models`) |
| Nemotron 3 Nano Omni | `nvidia/Nemotron-3-Nano-Omni` | **Removed 2026-08-31** (Aug 2026 deprecation table, replacement `nvidia/Nemotron-3_5-Lightning`). https://docs.tokenfactory.nebius.com/august-2026-deprecation-notice.md | Confirmed from docs |
| Cosmos 3 Super Reasoner | `nvidia/Cosmos3-Super-Reasoner` | **Removed 2026-08-31** (same table, replacement `MiniMaxAI/MiniMax-M3`). | Confirmed from docs |
| Cosmos Reason 2 | not listed on Token Factory in any page we found | Self-host on Nebius AI Cloud (Section 2C). | Confirmed from docs (absence: Confirm with account) |

Note: the Nov 2026 page also says "Update the `model` and `base_url` values in your API requests" when migrating; no new base URL is given, so we keep 1.1.

### 1B. Pricing

| Item | Finding | Source | Status |
|---|---|---|---|
| Per-model prices | The official pricing page (`https://nebius.com/token-factory/prices` redirects to `https://tokenfactory.nebius.com/organization/prices`) renders client-side and showed no prices to our fetcher. Third-party listings quote: Nano 30B $0.06 / $0.24, Super 120B $0.30 / $0.90, Ultra 550B $1.00 / $3.00 per 1M input / output tokens. | https://benchlm.ai/hosts/nebius (third party) | Confirm with account. Do not quote. The `verbose=true` model list returns a `pricing` object per model, which is the preferred source. |

## Token Factory

Live check section, written by `scripts/verify_token_factory.py` (it replaces this whole section). It has not run yet because we have no `NEBIUS_API_KEY`. Until it runs, every model ID above stays at the status shown in Section 1A.

## 2. Nebius AI Cloud: Serverless AI (jobs and endpoints)

| # | Finding | Source | Checked | Status |
|---|---|---|---|---|
| 2.1 | Serverless AI has three deployment types: Devlab, **Job** (non-interactive, runs to completion or timeout, no public URL, container disk removed when it finishes) and **Endpoint** (serves requests at a managed URL until stopped/deleted). | https://docs.nebius.com/serverless/overview.md | 2026-10-27 | Confirmed from docs |
| 2.2 | Submit a job: `nebius ai job create --name --image --container-command --args --platform --preset --timeout --subnet-id` plus `--env KEY=VALUE`, `--env-secret`, `--volume`, `--disk-size`, `--inject-file`, `--restart-policy never|on-failure`, `--preemptible` (requires `--follows-spot-price` or `--spot-pricing-policy-id`), `--async`, `--dry-run`, `--format json`. Status: `nebius ai job get <id>` / `nebius ai job get-by-name --name`, field `status.state`; logs: `nebius ai job logs <id>`; delete: `nebius ai job delete <id>`. | https://docs.nebius.com/cli/reference/ai/job/create.md, https://docs.nebius.com/serverless/quickstart/jobs.md | 2026-10-27 | Confirmed from docs |
| 2.3 | REST API: `POST https://api.nebius.cloud/ai/v1/jobs`, `GET/DELETE https://api.nebius.cloud/ai/v1/jobs/<id>` with a Bearer IAM token. SDKs exist for Go, Python and JavaScript. | https://docs.nebius.com/serverless/quickstart/jobs.md | 2026-10-27 | Confirmed from docs |
| 2.4 | **Max runtime**: `--timeout` minimum `1h`, maximum `168h`, default `24h`. Jobs cannot be stopped (only cancelled/deleted). | https://docs.nebius.com/cli/reference/ai/job/create.md, https://docs.nebius.com/serverless/pricing-quotas.md | 2026-10-27 | Confirmed from docs |
| 2.5 | **CPU-only jobs are supported**: the docs give a 20 GiB minimum disk "for CPU-only workloads" and a vCPU quota "if you use non-GPU VMs". Non-GPU platforms: `cpu-d3` (AMD EPYC Genoa, all regions; presets `2vcpu-8gb` … `128vcpu-512gb`, larger outside eu-north1/eu-south1) and `cpu-e2` (Intel Ice Lake, eu-north1). | https://docs.nebius.com/serverless/jobs/manage.md, https://docs.nebius.com/serverless/pricing-quotas.md, https://docs.nebius.com/compute/virtual-machines/types.md | 2026-10-27 | Confirmed from docs (run one `--dry-run` with `--platform cpu-d3` to confirm with account) |
| 2.6 | **GPU platforms** (`--platform` / preset): `gpu-l40s-a` and `gpu-l40s-d` (L40S 48 GB, eu-north1; e.g. `1gpu-8vcpu-32gb`, `1gpu-16vcpu-96gb`), `gpu-h100-sxm` (H100 80 GB, eu-north1, `1gpu-16vcpu-200gb`), `gpu-h200-sxm` (H200 141 GB, `1gpu-16vcpu-200gb`), `gpu-rtx6000` / `gpu-rtx6000-a` (RTX PRO 6000 96 GB, `1gpu-24vcpu-218gb`), `gpu-b200-sxm(-a)`, `gpu-b300-sxm`. CLI default platform is `gpu-h100-sxm` in eu-north1 and `gpu-h200-sxm` elsewhere; default preset is the smallest for the platform. | https://docs.nebius.com/compute/virtual-machines/types.md, https://docs.nebius.com/cli/reference/ai/job/create.md | 2026-10-27 | Confirmed from docs |
| 2.7 | **Concurrency**: Serverless AI has no own quotas; each running job or endpoint is one Compute VM and counts against Compute quotas (VMs, GPUs per platform, vCPUs for non-GPU, disks, disk capacity, allocations). Exceeding fails with `QuotaExceeded`. So parallel sim shards = min(VM quota, vCPU quota / preset vCPUs). | https://docs.nebius.com/serverless/pricing-quotas.md | 2026-10-27 | Confirmed from docs (our quota numbers: Confirm with account) |
| 2.8 | **Container requirements**: any image reference `registry/path:tag` or `@digest`; public registries or Nebius Container Registry in the same project need no credentials; private registries use `--registry-username/--registry-password` or `--registry-secret`. Disk at least 50 GiB for GPU, 20 GiB for CPU-only (default 250Gi). `/dev/shm` defaults to 16Gi on GPU platforms and 0 on CPU (`--shm-size`). Injected files are read-only and at most 64 KiB each. Docs examples use `nvidia/cuda:13.1.1-runtime-ubuntu24.04`. Image CPU architecture is not stated; all listed platforms are x86-64, so we build `linux/amd64`. | https://docs.nebius.com/serverless/jobs/manage.md, https://docs.nebius.com/cli/reference/ai/job/create.md | 2026-10-27 | Confirmed from docs (architecture: Confirm with account) |
| 2.9 | **Buckets in jobs**: `--volume s3://BUCKET:/container_path[:rw|ro[:PROFILE]]` mounts Object Storage; also `storagebucket-…:/path` by ID. Volumes persist across maintenance re-creation. We mount the bucket at `/data` and set `CLEARANCE_DATA=/data`. | https://docs.nebius.com/serverless/jobs/manage.md | 2026-10-27 | Confirmed from docs |
| 2.10 | Job statuses: `PROVISIONING`, `STARTING`, `IMAGE_PULLING`, `RUNNING`, `COMPLETED`, `CANCELLING`, `CANCELLED`, `DELETING`, `FAILED` (includes exceeded timeout), `ERROR`. | https://docs.nebius.com/serverless/lifecycle.md | 2026-10-27 | Confirmed from docs |
| 2.11 | **Endpoints**: `nebius ai endpoint create` takes the same flags plus `--container-port HOST[:CONTAINER][/http|tcp|udp]`, `--auth none|token`, `--token` / `--token-secret`, `--public`. Each HTTP port gets a managed `https://` URL (gRPC on the same URL); TCP ports get `tls://HOST:443`. URL lookup: `nebius ai endpoint get-by-name --name X --format json | jq -r '.status.public_endpoints[] | select(startswith("https://"))'`. A stopped endpoint is not billed. | https://docs.nebius.com/cli/reference/ai/endpoint/create.md, https://docs.nebius.com/serverless/tutorials/deploy-model.md | 2026-10-27 | Confirmed from docs |
| 2.12 | WebSocket support through the managed HTTPS URL is not stated (our chaos-mode `WS /live/{session}` needs it). Fallback: expose a TCP port (`tls://HOST:443`) or `--public` IP. | https://docs.nebius.com/cli/reference/ai/endpoint/create.md | 2026-10-27 | Confirm with account |
| 2.13 | Billing is per second for compute and storage while running (Compute VM and disk prices). | https://docs.nebius.com/serverless/pricing-quotas.md | 2026-10-27 | Confirmed from docs |
| 2.14 | CLI install: `curl -sSL https://artifacts.nebius.cloud/cli/install.sh \| bash`; default subnet: `nebius vpc subnet list --format jsonpath='{.items[0].metadata.id}'`. | https://docs.nebius.com/object-storage/quickstart.md, https://docs.nebius.com/serverless/tutorials/deploy-model.md | 2026-10-27 | Confirmed from docs |
| 2.15 | Container Registry: `nebius registry create`, `nebius registry configure-helper`, push to `cr.<region>.nebius.cloud/<registry-path>/<image>:<tag>`. | https://docs.nebius.com/container-registry/quickstart.md, https://docs.nebius.com/container-registry/authentication.md | 2026-10-27 | Confirmed from docs |
| 2.16 | Managed Service for PostgreSQL exists in Nebius AI Cloud. Availability in our project and region not checked. | https://docs.nebius.com/postgresql/quickstart.md | 2026-10-27 | Confirm with account |

### 2C. Self-hosted vLLM endpoint (Cosmos Reason 2)

| # | Finding | Source | Status |
|---|---|---|---|
| 2C.1 | Nebius tutorial deploys `vllm/vllm-openai:v0.18.0-cu130` as an endpoint with `--container-command "python3 -m vllm.entrypoints.openai.api_server" --args "--model <HF id> --host 0.0.0.0 --port 8000" --platform gpu-l40s-a --preset 1gpu-8vcpu-32gb --container-port 8000 --auth token --token $AUTH_TOKEN --shm-size 16Gi`. | https://docs.nebius.com/serverless/tutorials/deploy-model.md | Confirmed from docs |
| 2C.2 | Cosmos Reason 2 recommends `vllm>=0.11.0`; serve with `vllm serve nvidia/Cosmos-Reason2-<size> --max-model-len 16384 --reasoning-parser qwen3 --media-io-kwargs '{"video": {"num_frames": -1}}'`. Minimum GPU memory 24 GB (2B), 32 GB (8B). Tested on H100, GB200, DGX Spark ("works on Hopper and Blackwell"; L40S is Ada and not listed as validated). | https://github.com/nvidia-cosmos/cosmos-reason2 (README) | Confirmed from docs |
| 2C.3 | Cosmos-Reason2-2B and -8B are gated on Hugging Face (auto-approval), so the endpoint needs an `HF_TOKEN` (pass with `--env-secret`). 32B is not gated. | https://huggingface.co/api/models/nvidia/Cosmos-Reason2-8B | Confirmed from docs |
| 2C.4 | Choice for Clearance: `nvidia/Cosmos-Reason2-8B` on `gpu-h100-sxm` preset `1gpu-16vcpu-200gb` (validated GPU family). Definition in `infra/nebius/endpoints/cosmos_reason2.env`. | (decision) | Confirm with account (first start and latency) |

## 3. Nebius Object Storage (S3-compatible)

| # | Finding | Source | Checked | Status |
|---|---|---|---|---|
| 3.1 | S3 endpoint is `https://storage.<region>.nebius.cloud`, e.g. `https://storage.eu-north1.nebius.cloud`; region id is the Nebius region (`eu-north1`). | https://docs.nebius.com/object-storage/quickstart.md | 2026-10-27 | Confirmed from docs |
| 3.2 | Credentials: service-account access key (`nebius iam access-key create …`, AWS-style key id + secret), used as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`. HTTPS only by default. | https://docs.nebius.com/object-storage/quickstart.md, https://docs.nebius.com/object-storage/http-access.md | 2026-10-27 | Confirmed from docs |
| 3.3 | Supported: `GetObject`, `PutObject`, multipart upload, `ListObjectsV2`, `PutBucketCORS`. Not supported: object ACLs (use bucket policies), SSE-KMS/SSE-S3. ETag is not always MD5; use `X-Amz-Checksum-*`. | https://docs.nebius.com/object-storage/interfaces/s3-api-compatibility.md | 2026-10-27 | Confirmed from docs |
| 3.4 | **Presigned URLs**: no page on the current docs site (docs.nebius.com). The older docs site documents them (SigV4 query parameters, `aws s3 presign`, boto3 `generate_presigned_url` with `signature_version="s3v4"`), but against the older host `storage.ai.nebius.cloud`. Presigned URLs are computed on the client with SigV4, and `GetObject`/`PutObject` are supported, so they should work on the current endpoint. | https://nebius.ai/docs/storage/concepts/pre-signed-urls (older site) | 2026-10-27 | Confirm with account (one presigned PUT from a phone, one GET) |
| 3.5 | Browser/phone uploads with presigned PUT need a bucket CORS rule; `PutBucketCORS` is supported. | https://docs.nebius.com/object-storage/interfaces/s3-api-compatibility.md | 2026-10-27 | Confirmed from docs |

## 4. Tavily

| # | Finding | Source | Checked | Status |
|---|---|---|---|---|
| 4.1 | REST base `https://api.tavily.com/`; endpoints `POST /search`, `POST /extract`, `POST /crawl`, `POST /map`, `POST /research` (+ status and streaming). Auth: `Authorization: Bearer <key>`. Python SDK `tavily-python`, JS `@tavily/core`. | https://docs.tavily.com/documentation/api-reference/endpoint/search.md, https://docs.tavily.com/llms.txt | 2026-10-27 | Confirmed from docs |
| 4.2 | Rate limits: Development key 100 RPM, Production key 1,000 RPM; Crawl 100 RPM; Research create 20 RPM; Usage endpoint 10 per 10 min. 429 with `Retry-After`. Production keys need a paid plan or PAYGO. | https://docs.tavily.com/documentation/rate-limits.md | 2026-10-27 | Confirmed from docs |
| 4.3 | Free credits: 1,000 API credits per month, no credit card. Costs: basic search 1 credit, advanced search 2; extract 1 credit per 5 successful URLs (basic) or 2 per 5 (advanced); failed extractions are free. PAYGO $0.008 per credit. | https://docs.tavily.com/documentation/api-credits.md | 2026-10-27 | Confirmed from docs |
| 4.4 | Keyless Search/Extract exists (free, rate-limited). | https://docs.tavily.com/documentation/keyless.md (index entry) | 2026-10-27 | Confirmed from docs |
| 4.5 | Tavily describes itself as "part of the Nebius product portfolio". Hackathon-specific Tavily credits: not stated. | https://docs.tavily.com/llms.txt | 2026-10-27 | Confirmed from docs (credits: Confirm with account) |
| 4.6 | Best Use of Tavily eligibility requires "a functional, runtime call to the Tavily API as part of its solution". | Rules, Section 8 | 2026-10-27 | Confirmed from docs |

## 5. NVIDIA models referenced by us and by other teams

| Model | Exists? | Where served | License | Model card | Status |
|---|---|---|---|---|---|
| Nemotron 3 Nano 30B-A3B | Yes | Token Factory until 2026-11-09 (1A) | NVIDIA Nemotron Open Model License | https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16 | Confirmed from docs |
| Nemotron 3 Super 120B-A12B | Yes | Token Factory until 2026-11-09 | NVIDIA Nemotron Open Model License | https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16 | Confirmed from docs |
| Nemotron 3 Ultra 550B-A55B | Yes | Token Factory (ID: Confirm with account) | OpenMDW-1.1 | https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16 | Confirmed from docs (ID: Confirm with account) |
| **Nemotron 3.5 Super VL** (120B-A12B) | Yes (HF repos created 2026-10-12: BF16, NVFP4, Base-BF16) | Token Factory as `nvidia/Nemotron-3.5-Super-VL` (image2text) | OpenMDW-1.1 | https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16 | Confirmed from docs |
| Nemotron 3.5 Lightning 30B-A3B | Yes | Token Factory as `nvidia/Nemotron-3_5-Lightning` | OpenMDW-1.1 | https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16 | Confirmed from docs |
| **Cosmos Reason 2** (2B, 8B, 32B) | Yes | Not on Token Factory; self-host with vLLM on Nebius AI Cloud (2C) | NVIDIA Open Model License; built on Qwen3-VL (Apache-2.0) | https://huggingface.co/nvidia/Cosmos-Reason2-8B | Confirmed from docs |
| **Cosmos 3** (Cosmos3-Nano, Cosmos3-Super, Edge, Image2Video, Text2Image, policy variants) | Yes | Self-host only; `nvidia/Cosmos3-Super-Reasoner` was on Token Factory but was removed 2026-08-31 | OpenMDW-1.1 (Cosmos3-Nano and Cosmos3-Super cards) | https://huggingface.co/nvidia/Cosmos3-Super, https://huggingface.co/nvidia/Cosmos3-Nano | Confirmed from docs |
| **GR00T N1.7** (3B; DROID, LIBERO, SimplerEnv fine-tunes) | Yes (`nvidia/GR00T-N1.7-3B`, created 2026-02-25) | Self-host only (robot policy, not a chat model) | NVIDIA Open Model License (card "License" section) | https://huggingface.co/nvidia/GR00T-N1.7-3B | Confirmed from docs |
| Nemotron 3 Nano Omni 30B-A3B | Yes on HF | Removed from Token Factory 2026-08-31 | NVIDIA Open Model Agreement | https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-BF16 | Confirmed from docs |

**Scene model decision (PLAN v1.2):** primary = Cosmos Reason 2 (8B) self-hosted on a Nebius Serverless Endpoint via vLLM; alternative = `nvidia/Nemotron-3.5-Super-VL` on Token Factory (no GPU to manage, and it also serves as the Super successor).

## 6. Hackathon official rules (Devpost)

Source for every row: https://nebiusglobalaihackathon.devpost.com/rules (Official Rules), read 2026-10-27. The overview page is https://nebiusglobalaihackathon.devpost.com/.

| # | Finding | Status |
|---|---|---|
| 6.1 | **Deadline**: Submission Period ends **Friday 2026-10-30, 10:00 am Pacific Time** (PDT, UTC-7) = **10:30 pm IST** on 2026-10-30. Started 2026-08-26 9:00 am PT. | Confirmed from docs |
| 6.2 | Judging Period 2026-12-01 9:00 am PT to 2026-12-15 12:00 pm PT. Winners announced on or around 2027-01-11. The Project must stay available free of charge for testing "until the Judging Period ends". | Confirmed from docs |
| 6.3 | **Team size**: the rules allow individuals, Teams and Organizations and state no maximum team size. A Team appoints one Representative who submits; prizes go to the Representative. A person may be on more than one Team and also enter individually. | Confirmed from docs |
| 6.4 | **Prize stacking**: the rules say, verbatim, "Each Project is eligible for one (1) Overall Award OR one (1) Track Award and one (1) Bonus Award." The table lists Bonus Awards as Best Use of Tavily ($3,000), City Winner ($500 x20) and Most Valuable Feedback ($100 + NVIDIA swag x10). Two readings: (a) Overall alone, or Track + Bonus; (b) one of Overall/Track, plus one Bonus. Under both readings a Project can win at most one of Overall/Track and cannot win two Bonus Awards (for example Tavily and Feedback). Under reading (a) an Overall winner gets no Bonus Award. | Confirmed from docs (meaning: send a written clarification request; the rules say entrants "must submit a written request for clarification" before the deadline if a term is ambiguous) |
| 6.5 | Prizes: Grand $20,000; 2nd $10,000; 3rd $6,000; each of 4 tracks an NVIDIA Jetson Orin Nano; Best Use of Tavily $3,000; City Winner $500 (20, only for entrants attending a listed Builders & Brews city event); Most Valuable Feedback $100 + swag (10). | Confirmed from docs |
| 6.6 | **What to build**: a working software application that runs on Nebius Token Factory or Nebius AI Cloud and uses at least one NVIDIA open source model, in one of four tracks. The Physical AI track text names "Nemotron, GROOT, Cosmos and Sonic models", Nebius Serverless Jobs to run simulations, generate data, evaluate robot policies or process sensor data at scale, and Serverless Endpoints for real-time inference. | Confirmed from docs |
| 6.7 | **Submission requirements**: demo URL (not required for Physical AI); text description; public GitHub/GitLab/Bitbucket repo with all source, assets and instructions; an open source license file detectable in the repo About section (Apache 2.0, MIT or MPL 2.0 given as examples); README with setup and run instructions highlighting Nemotron / NVIDIA models, where Token Factory helped, and other Nebius services; video under 3 minutes on public YouTube with footage of the project on its device; for Physical AI at least 1 minute of physical hardware operating (or key modules in action when there is no hardware); track selection; feedback on Token Factory, AI Cloud and NVIDIA tools; English materials. | Confirmed from docs |
| 6.8 | **Video content**: must not include third-party trademarks or copyrighted music or other material without permission. | Confirmed from docs |
| 6.9 | **Third-party data and integrations**: entrants must be authorized to use any third-party SDK, API or data under its terms. Submissions must not violate others' copyright, trademark, patent, contract or **privacy rights**; content must not be subject to third-party rights "including privacy and publicity rights" unless the entrant owns them or has permission. Open source use is allowed if licenses are respected. | Confirmed from docs |
| 6.10 | **Filming people**: no rule specific to filming the public. The privacy/publicity warranty in 6.9 applies, so anyone identifiable in the video or published data needs permission. Sponsor and Devpost may later allow edits that remove personally identifiable information. Our approach: docs/PRIVACY.md. | Confirmed from docs |
| 6.11 | **New and existing projects**: new, or significantly updated during the Submission Period with an explanation. | Confirmed from docs |
| 6.12 | Judging: Stage One pass/fail on theme fit and use of the required APIs; Stage Two equally weighted Technological Implementation, Design, Potential Impact, Quality of the Idea. Feedback prize judged on completeness, viability and potential impact. | Confirmed from docs |
| 6.13 | Not eligible: residents of Brazil, Quebec, Russia, Crimea, Cuba, Iran, North Korea and other OFAC-sanctioned regions; Sponsor/Administrator staff and families; judges and their employers. | Confirmed from docs |
| 6.14 | Sponsor and Devpost may use the name, likeness, voice and image of all contributors in promotion for three years. | Confirmed from docs |

## 7. Open items (Confirm with account)

| Item | How to confirm |
|---|---|
| Token Factory model IDs actually served, incl. Ultra and the 3.5 successors | `python scripts/verify_token_factory.py` with `NEBIUS_API_KEY` |
| Per-model price, context length, tool-calling / JSON support | `GET /v1/models?verbose=true` |
| Base rate limits for our project | Token Factory console, Rate Limits section |
| Serverless CPU job on `cpu-d3` | `bash infra/nebius/submit_sim_shards.sh --dry-run` |
| Compute quotas (VMs, vCPUs, GPUs per platform) | console.nebius.com/quota |
| Presigned PUT/GET on `storage.eu-north1.nebius.cloud` | `bash infra/nebius/create_bucket.sh` then one upload from a phone |
| WebSocket through the endpoint's managed HTTPS URL | open `wss://<endpoint>/live/<session>` after `deploy_api_endpoint.sh` |
| Hackathon credits for Nebius and Tavily | account dashboards |
| Prize stacking meaning | written clarification request to the organizers |
