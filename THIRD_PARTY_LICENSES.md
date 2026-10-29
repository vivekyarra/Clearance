# Third-party licenses

Clearance is licensed under Apache-2.0 (see `LICENSE`). This file lists the third-party software and models Clearance uses or can use, with the license confirmed at the primary source (repository LICENSE file or Hugging Face model card) on 2026-10-27. Versions are the ones pinned in `requirements.txt`, `requirements-gpu.txt` and `apps/web/package.json`.

Rules we follow:
- Only permissive licenses (Apache-2.0, MIT, BSD, ISC, MIT-CMU) or NVIDIA open model licenses for anything we ship or run.
- Non-commercial and network-copyleft components are excluded (see "Excluded" below).
- Robot shapes are drawn from spec dimensions; no vendor meshes, logos or photos are bundled.

## Python runtime (requirements.txt)

| Package | Version | License | Source |
|---|---|---|---|
| NumPy | 2.4.6 | BSD-3-Clause (wheels also bundle 0BSD, MIT, Zlib, CC0-1.0 parts) | https://github.com/numpy/numpy/blob/main/LICENSE.txt |
| SciPy | 1.17.1 | BSD-3-Clause | https://github.com/scipy/scipy/blob/main/LICENSE.txt |
| Numba | 0.68.0 | BSD-2-Clause | https://github.com/numba/numba/blob/main/LICENSE |
| llvmlite | 0.50.0 | BSD-2-Clause | https://github.com/numba/llvmlite/blob/main/LICENSE |
| Shapely | 2.2.0 | BSD-3-Clause | https://github.com/shapely/shapely/blob/main/LICENSE.txt |
| opencv-python-headless (OpenCV) | 5.0.0.93 | Apache-2.0 (OpenCV 4.5.0 and later; 4.4.0 and earlier were BSD-3-Clause) | https://github.com/opencv/opencv/blob/4.x/LICENSE |
| OpenCV ArUco / AprilTag detector (part of OpenCV objdetect) | (with OpenCV) | Apache-2.0; source headers keep a University of Michigan copyright notice for the AprilTag code | https://github.com/opencv/opencv/tree/4.x/modules/objdetect/src/aruco/apriltag |
| FastAPI | 0.162.0 | MIT | https://github.com/fastapi/fastapi/blob/master/LICENSE |
| Starlette | 1.10.0 | BSD-3-Clause | https://github.com/encode/starlette/blob/master/LICENSE.md |
| Uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/Kludex/uvicorn/blob/main/LICENSE.md |
| Pydantic | 2.14.1 | MIT | https://github.com/pydantic/pydantic/blob/main/LICENSE |
| SQLAlchemy | 2.1.6 | MIT | https://github.com/sqlalchemy/sqlalchemy/blob/main/LICENSE |
| httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx/blob/master/LICENSE.md |
| openai (Python SDK, used as the OpenAI-compatible client for Token Factory and vLLM) | 3.39.0 | Apache-2.0 | https://github.com/openai/openai-python/blob/main/LICENSE |
| tavily-python | 0.8.5 | MIT | https://github.com/tavily-ai/tavily-python/blob/master/LICENSE |
| PyYAML | 6.0.3 | MIT | https://github.com/yaml/pyyaml/blob/main/LICENSE |
| Pillow | 13.0.0 | MIT-CMU (HPND variant) | https://github.com/python-pillow/Pillow/blob/main/LICENSE |
| jsonschema | 4.26.0 | MIT | https://github.com/python-jsonschema/jsonschema/blob/main/COPYING |
| qrcode | 8.2 | BSD-3-Clause style (Lincoln Loop) | https://github.com/lincolnloop/python-qrcode/blob/main/LICENSE |
| fpdf2 | 2.8.9 | LGPL-3.0-only (used unmodified as a library) | https://github.com/py-pdf/fpdf2/blob/master/LICENSE |
| pypdf | 6.21.3 | BSD-3-Clause | https://github.com/py-pdf/pypdf/blob/main/LICENSE |
| pytest (tests only) | 9.1.1 | MIT | https://github.com/pytest-dev/pytest/blob/main/LICENSE |
| boto3 (requirements-cloud.txt; used when `CLEARANCE_S3_BUCKET` is set) | 1.43.119 | Apache-2.0 | https://github.com/boto/boto3/blob/develop/LICENSE |
| psycopg[binary] (requirements-cloud.txt; used with a Postgres `CLEARANCE_DATABASE_URL`) | 3.3.6 | LGPL-3.0-only (used unmodified as a library) | https://github.com/psycopg/psycopg/blob/master/LICENSE.txt |
| PySocialForce (pedestrian model, optional) | 1.1.2 | MIT | https://github.com/yuxiang-gao/PySocialForce/blob/master/LICENSE |

fpdf2 and psycopg are LGPL-3.0: we import them as unmodified, separately installed libraries, which LGPL permits from Apache-2.0 code. Neither is vendored into this repository. (fpdf2's `LGPL-3.0-only` was read from its installed package metadata, `License-Expression`.)

## Python GPU jobs (requirements-gpu.txt, Dockerfile.jobs-gpu)

| Package | License | Source | Notes |
|---|---|---|---|
| gsplat | Apache-2.0 | https://github.com/nerfstudio-project/gsplat/blob/main/LICENSE | Splat training (J3) |
| Open3D | MIT | https://github.com/isl-org/Open3D/blob/main/LICENSE | TSDF fusion, point clouds (J2/J4) |
| Grounding DINO | Apache-2.0 | https://github.com/IDEA-Research/GroundingDINO/blob/main/LICENSE | HF port `IDEA-Research/grounding-dino-tiny` is also apache-2.0 |
| SAM 2 (code and checkpoints) | Apache-2.0 | https://github.com/facebookresearch/sam2/blob/main/LICENSE | Demo fonts in that repo are OFL-1.1; not used |
| ByteTrack | MIT | https://github.com/FoundationVision/ByteTrack/blob/main/LICENSE | Repo moved from ifzhang/ByteTrack; we use the tracker logic only, not its YOLOX detector |
| PyTorch | BSD-3-Clause style (repo LICENSE); the PyPI wheel metadata also declares Apache-2.0 bundled components | https://github.com/pytorch/pytorch/blob/main/LICENSE | Runtime for the GPU models |
| Hugging Face transformers | Apache-2.0 | https://github.com/huggingface/transformers/blob/main/LICENSE | Model loading |
| vLLM (vision endpoint image) | Apache-2.0 | https://github.com/vllm-project/vllm/blob/main/LICENSE | `vllm/vllm-openai` container |
| usd-core (OpenUSD) | Tomorrow Open Source Technology License 1.0 (`LicenseRef-TOST-1.0`) | https://github.com/PixarAnimationStudios/OpenUSD/blob/release/LICENSE.txt | Apache-2.0 with a narrower Section 6 (Trademarks): trademark use only as needed to comply with Section 4(c) and reproduce NOTICE |

## Models

| Model | License | Source | Notes |
|---|---|---|---|
| Depth Anything V2 **Small** | Apache-2.0 | https://huggingface.co/depth-anything/Depth-Anything-V2-Small, https://github.com/DepthAnything/Depth-Anything-V2 | The only size we use |
| Nemotron 3 Nano 30B-A3B | NVIDIA Nemotron Open Model License | https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16 | Commercial use allowed; keep the "Licensed by NVIDIA Corporation under the NVIDIA Nemotron Model License" notice if a NOTICE file is present |
| Nemotron 3 Super 120B-A12B | NVIDIA Nemotron Open Model License | https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16 | Called through Token Factory |
| Nemotron 3 Ultra 550B-A55B | OpenMDW-1.1 | https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16, https://openmdw.ai/license/1-1/ | Called through Token Factory |
| Nemotron 3.5 Super VL 120B-A12B | OpenMDW-1.1 | https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16 | Alternative scene model on Token Factory |
| Nemotron 3.5 Lightning 30B-A3B | OpenMDW-1.1 | https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16 | Nano-tier successor on Token Factory |
| Cosmos Reason 2 (2B / 8B / 32B) | NVIDIA Open Model License | https://huggingface.co/nvidia/Cosmos-Reason2-8B, https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license | Primary scene model, self-hosted with vLLM. Built on Qwen3-VL (Apache-2.0). Requires the "Licensed by NVIDIA Corporation under the NVIDIA Open Model License" notice and **"Built on NVIDIA Cosmos"** attribution in the product and README. Rights end if safety guardrails are bypassed. 2B and 8B are gated (auto-approve). |
| Cosmos 3 (Nano / Super) | OpenMDW-1.1 | https://huggingface.co/nvidia/Cosmos3-Super | Referenced only; not used in the MVP |
| GR00T N1.7 3B | NVIDIA Open Model License | https://huggingface.co/nvidia/GR00T-N1.7-3B | Referenced only; humanoid policies are out of MVP scope |
| MediaPipe BlazeFace short-range face detector | Apache-2.0 per its model card (linked from the MediaPipe Face Detector page) | https://developers.google.com/edge/mediapipe/solutions/vision/face_detector | Confirm the model card wording before release |

## Web (apps/web/package.json)

| Package | Version | License | Source |
|---|---|---|---|
| React, react-dom | 18.3.1 | MIT | https://github.com/facebook/react/blob/main/LICENSE |
| react-router-dom | 6.30.6 | MIT | https://github.com/remix-run/react-router/blob/main/LICENSE.md |
| three.js | 0.170.0 | MIT | https://github.com/mrdoob/three.js/blob/dev/LICENSE |
| GaussianSplats3D (`@mkkellogg/gaussian-splats-3d`) | 0.4.7 | MIT | https://github.com/mkkellogg/GaussianSplats3D/blob/main/LICENSE |
| qrcode (npm) | 1.5.4 | MIT | https://github.com/soldair/node-qrcode/blob/master/license |
| Vite (dev) | 6.4.5 | MIT | https://github.com/vitejs/vite/blob/main/LICENSE |
| @vitejs/plugin-react (dev) | 4.7.0 | MIT | https://github.com/vitejs/vite-plugin-react/blob/main/LICENSE |
| TypeScript (dev) | 5.9.3 | Apache-2.0 | https://github.com/microsoft/TypeScript/blob/main/LICENSE.txt |
| Vitest (dev) | 3.2.7 | MIT | https://github.com/vitest-dev/vitest/blob/main/LICENSE |
| MediaPipe Tasks Vision (`@mediapipe/tasks-vision`, wasm bundled with the build) | 0.10.14 | Apache-2.0 | https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE |
| BlazeFace short-range face model (`blaze_face_short_range.tflite`, float16 v1, bundled) | v1 | Apache-2.0 | https://ai.google.dev/edge/mediapipe/solutions/vision/face_detector (model card) |
| OpenCV.js (`@techstark/opencv-js`, bundled with the build) | 4.10.0-release.1 | Apache-2.0 | https://github.com/TechStark/opencv-js/blob/main/LICENSE, https://github.com/opencv/opencv/blob/4.x/LICENSE |

## Excluded (license does not fit an Apache-2.0 product)

| Component | License | Source | Reason |
|---|---|---|---|
| Ultralytics YOLO | AGPL-3.0 | https://github.com/ultralytics/ultralytics/blob/main/LICENSE | Network copyleft |
| DUSt3R | CC BY-NC-SA 4.0 | https://github.com/naver/dust3r/blob/main/LICENSE | Non-commercial |
| MASt3R | CC BY-NC-SA 4.0 | https://github.com/naver/mast3r/blob/main/LICENSE | Non-commercial |
| Depth Anything V2 Base / Large / Giant | CC BY-NC 4.0 (Giant weights not released) | https://github.com/DepthAnything/Depth-Anything-V2 | Non-commercial |

## Attribution notices to reproduce

- "Built on NVIDIA Cosmos" (Cosmos Reason 2, NVIDIA Open Model License).
- "Licensed by NVIDIA Corporation under the NVIDIA Open Model License" / "… under the NVIDIA Nemotron Model License" where the model ships a NOTICE file.
- OpenMDW-1.1 models: keep the license agreement and notices with any redistributed weights (we do not redistribute weights).
