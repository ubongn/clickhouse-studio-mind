"""Deploy ClickHouse Studio Mind to Cloud Run — pure REST, no gcloud CLI.

Auth: a service-account key file (google-auth token mint). On this project the
deploying identity needs roles: run.admin, cloudbuild.builds.editor,
artifactregistry.writer, storage.objectAdmin (+ serviceAccountUser on the
runtime SA). The RUNTIME identity inside the container is different and
narrower (vertex-runner, aiplatform.user only) — it never sees a key.

Chain (all via REST, token from --key / GOOGLE_APPLICATION_CREDENTIALS):
  1. ensure Artifact Registry docker repo  <region>-docker.pkg.dev/<proj>/studio-mind
  2. ensure GCS bucket <proj>-studio-mind-deploy, upload `git archive HEAD` tar
  3. Cloud Build: docker build + push (official Dockerfile in repo root)
  4. Cloud Run v2: create-or-replace from deploy/service.yaml
     (image digest + __CLICKHOUSE_PASSWORD__ substituted from .env)
  5. setIamPolicy: allUsers -> roles/run.invoker  (unauthenticated judges)
  6. GET /health on the public URL

Usage:
  python deploy/deploy.py --region europe-west6
  python deploy/deploy.py --region us-central1 --tag live2 --no-public
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml  # PyYAML — only needed for deploy, kept out of runtime deps
except ImportError:  # pragma: no cover
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_DEFAULT = "agentic-cinema-506710"
SERVICE = "studio-mind"
REPO_ID = "studio-mind"


class Api:
    def __init__(self, key_path: str, project: str):
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        self.project = project
        self.creds = service_account.Credentials.from_service_account_file(
            key_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.creds.refresh(Request())

    def _refresh(self):
        if self.creds.expired:
            from google.auth.transport.requests import Request
            self.creds.refresh(Request())

    def call(self, method: str, url: str, body: dict | None = None, *,
             raw: bytes | None = None, content_type: str = "application/json"):
        self._refresh()
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.creds.token}",
            "Content-Type": content_type,
        })
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if payload and content_type == "application/json" else payload)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read())
            except Exception:
                detail = {}
            return e.code, detail

    def die_on(self, status: int, detail, what: str):
        msg = detail.get("error", {}).get("message", str(detail)) if isinstance(detail, dict) else str(detail)
        print(f"[FAIL] {what}: HTTP {status} — {msg}", file=sys.stderr)
        if status == 403:
            print(
                "\nThe deploying identity lacks a required IAM role. Grant (as project owner):\n"
                f"  gcloud projects add-iam-policy-binding {self.project} \\\n"
                "      --member=serviceAccount:<DEPLOYER_SA> --role=roles/run.admin\n"
                "      ... plus roles/cloudbuild.builds.editor, roles/artifactregistry.writer,\n"
                "      roles/storage.objectAdmin, roles/iam.serviceAccountUser\n"
                "and enable: run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com",
                file=sys.stderr)
        sys.exit(1)


def op_url_of(op: dict, base: str) -> str:
    """Absolute polling URL for a long-running operation."""
    if op.get("selfLink") and str(op["selfLink"]).startswith("http"):
        return op["selfLink"]
    return base + op["name"]


def poll(api: Api, url: str, what: str, every: int = 5, limit: int = 600):
    """Poll a long-running operation until done."""
    t0 = time.time()
    while time.time() - t0 < limit:
        s, op = api.call("GET", url)
        if s != 200:
            api.die_on(s, op, f"polling {what}")
        if op.get("done"):
            if "error" in op:
                print(f"[FAIL] {what} failed: {op['error'].get('message')}", file=sys.stderr)
                sys.exit(1)
            print(f"[ok] {what} done in {time.time()-t0:.0f}s")
            return op
        time.sleep(every)
    print(f"[FAIL] {what}: timed out after {limit}s", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="europe-west6",
                    help="Cloud Run region (default europe-west6, near the ClickHouse Cloud service)")
    ap.add_argument("--project", default=PROJECT_DEFAULT)
    ap.add_argument("--key", default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
                    help="deployer service-account JSON (default $GOOGLE_APPLICATION_CREDENTIALS)")
    ap.add_argument("--tag", default="live")
    ap.add_argument("--no-public", action="store_true", help="skip allUsers invoker binding")
    args = ap.parse_args()

    if not args.key:
        sys.exit("[FAIL] no deploy key: pass --key or set GOOGLE_APPLICATION_CREDENTIALS")
    if yaml is None:
        sys.exit("[FAIL] PyYAML missing: pip install pyyaml")

    # ---- secrets from local .env (never committed) ---------------------------
    env = {}
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    ch_pw = env.get("CLICKHOUSE_PASSWORD") or os.environ.get("CLICKHOUSE_PASSWORD")
    if not ch_pw:
        sys.exit("[FAIL] CLICKHOUSE_PASSWORD not found in .env / environment")

    api = Api(args.key, args.project)
    region, proj = args.region, args.project
    image = f"{region}-docker.pkg.dev/{proj}/{REPO_ID}/{SERVICE}:{args.tag}"
    print(f"[i] deploying {SERVICE} -> {region}, image {image}")

    # ---- 1. Artifact Registry repo ------------------------------------------
    ar_parent = f"projects/{proj}/locations/{region}"
    ar_base = f"https://artifactregistry.googleapis.com/v1/{ar_parent}/repositories"
    s, b = api.call("GET", f"{ar_base}/{REPO_ID}")
    if s == 404:
        s, b = api.call("POST", f"{ar_base}?repositoryId={REPO_ID}",
                        {"format": "DOCKER", "description": "ClickHouse Studio Mind"})
        if s not in (200, 201):
            api.die_on(s, b, "artifactregistry.repositories.create")
        print("[ok] artifact registry repo created")
    elif s != 200:
        api.die_on(s, b, "artifactregistry.repositories.get")
    else:
        print("[ok] artifact registry repo exists")

    # ---- 2. source tarball -> GCS --------------------------------------------
    bucket = f"{proj}-studio-mind-deploy"
    s, b = api.call("GET", f"https://storage.googleapis.com/storage/v1/b/{bucket}")
    if s == 404:
        s, b = api.call("POST", "https://storage.googleapis.com/storage/v1/b?project=" + proj,
                        {"name": bucket, "location": region, "uniformBucketLevelAccess": {"enabled": True}})
        if s not in (200, 201):
            api.die_on(s, b, "storage.buckets.create")
        print("[ok] source bucket created")
    elif s != 200:
        api.die_on(s, b, "storage.buckets.get")
    else:
        print("[ok] source bucket exists")

    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "src.tar.gz"
        # git archive = exactly the committed tree; secrets/local noise never ship
        r = subprocess.run(["git", "archive", "--format=tar.gz", "-o", str(tar_path), "HEAD"],
                           cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            sys.exit(f"[FAIL] git archive: {r.stderr}")
        with tarfile.open(tar_path) as tf:
            n = len(tf.getnames())
        stamp = time.strftime("%Y%m%d-%H%M%S")
        obj = f"src/{stamp}.tar.gz"
        s, b = api.call("POST", f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
                        f"?uploadType=media&name={obj}",
                        raw=tar_path.read_bytes(), content_type="application/gzip")
        if s not in (200, 201):
            api.die_on(s, b, "source upload")
        print(f"[ok] source uploaded: gs://{bucket}/{obj} ({n} files)")

        # ---- 3. Cloud Build ------------------------------------------------------
        build = {
            "source": {"storageSource": {"bucket": bucket, "object": obj}},
            "steps": [
                {"name": "gcr.io/cloud-builders/docker",
                 "args": ["build", "-t", image, "."]},
                {"name": "gcr.io/cloud-builders/docker",
                 "args": ["push", image]},
            ],
            "images": [image],
            "options": {"logging": "CLOUD_LOGGING_ONLY"},
        }
        s, b = api.call("POST",
                        f"https://cloudbuild.googleapis.com/v1/projects/{proj}/locations/{region}/builds",
                        build)
        if s not in (200, 201):
            api.die_on(s, b, "cloudbuild.builds.create")
        print(f"[ok] cloud build submitted: {b.get('metadata', {}).get('build', {}).get('id', b.get('name', ''))}")
        poll(api, op_url_of(b, "https://cloudbuild.googleapis.com/v1/"),
             "cloud build", every=8, limit=900)

    # ---- 4. Cloud Run v2 create-or-replace ------------------------------------
    svc = yaml.safe_load((REPO_ROOT / "deploy" / "service.yaml").read_text(encoding="utf-8"))
    svc.pop("apiVersion", None)
    svc.pop("kind", None)
    svc["metadata"] = {"name": SERVICE, "labels": {"app": SERVICE}}
    for c in svc["spec"]["template"]["spec"]["containers"]:
        if c.get("image") == "__IMAGE__":
            c["image"] = image
        for e in c.get("env", []):
            if e.get("value") == "__CLICKHOUSE_PASSWORD__":
                e["value"] = ch_pw

    base = f"https://{region}-run.googleapis.com/v2/projects/{proj}/locations/{region}/services"
    s, b = api.call("GET", f"{base}/{SERVICE}")
    exists = s == 200
    if exists:
        s, b = api.call("PATCH", f"{base}/{SERVICE}",
                        {"template": svc["spec"]["template"]})   # spec immutable except template
    else:
        s, b = api.call("POST", f"{base}?serviceId={SERVICE}", svc)
    if s not in (200, 201):
        api.die_on(s, b, "run.services." + ("update" if exists else "create"))
    print(f"[ok] cloud run {'updated' if exists else 'created'} (revision deploying)")
    op_base = f"https://{region}-run.googleapis.com/v2/"
    poll(api, op_url_of(b, op_base), "cloud run revision")

    # ---- 5. public invoker ------------------------------------------------------
    if not args.no_public:
        policy = {
            "policy": {"bindings": [{
                "role": "roles/run.invoker",
                "members": ["allUsers"],
            }]}
        }
        s, b = api.call("POST", f"{base}/{SERVICE}:setIamPolicy", policy)
        if s in (200, 201):
            print("[ok] public unauthenticated access enabled (allUsers -> run.invoker)")
        else:
            print(f"[warn] setIamPolicy: HTTP {s} — service is deployed; make it public "
                  f"with: gcloud run services add-iam-policy-binding {SERVICE} "
                  f"--region {region} --member=allUsers --role=roles/run.invoker",
                  file=sys.stderr)

    # ---- 6. verify ---------------------------------------------------------------
    s, b = api.call("GET", f"{base}/{SERVICE}")
    url = b.get("uri", "") if isinstance(b, dict) else ""
    print(f"\n[i] service URL: {url}")
    if url:
        time.sleep(5)
        s, b = api.call("GET", f"{url}/health")
        print(f"[i] GET {url}/health -> {s} {b if isinstance(b, dict) else ''}")
    print(f"\nLIVE URL: {url}")


if __name__ == "__main__":
    main()
