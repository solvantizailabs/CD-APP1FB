"""
Two interchangeable worker-fleet backends behind one interface, selected by
CONTROLLER_MODE:

- "digitalocean" (real): creates/destroys real Droplets via the DigitalOcean
  API (Part W/X/Y). Needs DO_API_TOKEN, DO_REGION, DO_WORKER_SIZE,
  DO_WORKER_SNAPSHOT_ID as env vars/secrets - never hardcoded (Part W).

- "local" (this session's explicit ask): starts/stops extra
  `hyperframe-worker` containers on the same Docker network via the Docker
  Engine API, instead of real Droplets - same scaling.py decision logic, same
  Controller code path, zero DigitalOcean calls and zero cost, so the whole
  Redis-queue-depth -> worker-count -> create/destroy loop (Part Z's Test 1-3)
  can be proven correct in docker-compose before any DO account exists.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

MODE = os.getenv("CONTROLLER_MODE", "local")


class LocalDockerFleet:
    """Local stand-in for DigitalOcean Droplets: extra containers from the
    same hyperframe-worker image, on the same docker-compose network."""

    def __init__(self):
        import docker
        self.client = docker.from_env()
        self.image = os.getenv("LOCAL_WORKER_IMAGE", "dronax-hyperframe-worker:local")
        self.network = os.getenv("LOCAL_WORKER_NETWORK", "dronax_local")
        self.label = "dronax.hyperframe.managed"

    def list_workers(self) -> list:
        return self.client.containers.list(
            all=True, filters={"label": f"{self.label}=true"}
        )

    def create_worker(self, env: dict) -> str:
        name = f"hf-worker-{int(time.time() * 1000)}"
        logger.info(f"[local-fleet] Starting local worker container {name}")
        volumes = {}
        host_key_path = os.getenv("HOST_SERVICE_ACCOUNT_KEY_PATH")
        if host_key_path:
            volumes[host_key_path] = {"bind": "/app/serviceAccountKey.json", "mode": "ro"}
        container = self.client.containers.run(
            self.image,
            name=name,
            environment=env,
            network=self.network,
            labels={self.label: "true"},
            volumes=volumes,
            detach=True,
        )
        return container.id

    def destroy_worker(self, container_id: str) -> None:
        logger.info(f"[local-fleet] Stopping+removing local worker container {container_id[:12]}")
        try:
            c = self.client.containers.get(container_id)
            c.stop(timeout=10)
            c.remove()
        except Exception as e:
            logger.warning(f"[local-fleet] Could not remove {container_id[:12]}: {e}")


class DigitalOceanFleet:
    """Real Droplets via the DigitalOcean API (Part R/W/X/Y). Uses plain
    httpx against the documented v2 REST API rather than a wrapper SDK, to
    keep this file's dependency footprint identical to the rest of this
    codebase's DO-facing services."""

    API_BASE = "https://api.digitalocean.com/v2"

    def __init__(self):
        import httpx
        self.token = os.environ["DO_API_TOKEN"]  # Part W: never hardcoded, secret/env only
        self.region = os.getenv("DO_REGION", "blr1")
        self.size = os.getenv("DO_WORKER_SIZE", "c-2vcpu-4gb")
        self.image = os.getenv("DO_WORKER_SNAPSHOT_ID")  # custom snapshot, Part 11
        self.tag = "dronax-hyperframe-worker"
        self.client = httpx.Client(
            base_url=self.API_BASE,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            timeout=30,
        )

    def list_workers(self) -> list:
        res = self.client.get("/droplets", params={"tag_name": self.tag})
        res.raise_for_status()
        return res.json().get("droplets", [])

    def create_worker(self, env: dict) -> str:
        user_data = _cloud_init_script(env)
        res = self.client.post("/droplets", json={
            "name": f"hf-worker-{int(time.time())}",
            "region": self.region,
            "size": self.size,
            "image": self.image,
            "tags": [self.tag],
            "user_data": user_data,
            "vpc_uuid": os.getenv("DO_VPC_UUID"),  # set at creation only - can't be added later
        })
        res.raise_for_status()  # 202 Accepted
        return str(res.json()["droplet"]["id"])

    def destroy_worker(self, droplet_id: str) -> None:
        # DELETE, not power-off - v5 Droplets bill until destroyed (Part Y).
        res = self.client.delete(f"/droplets/{droplet_id}")
        if res.status_code not in (204, 404):
            res.raise_for_status()


def _cloud_init_script(env: dict) -> str:
    env_lines = "\n".join(f'export {k}="{v}"' for k, v in env.items())
    return f"""#cloud-config
runcmd:
  - |
    {env_lines}
    docker pull {os.getenv('DO_WORKER_IMAGE', 'registry.digitalocean.com/dronax-registry/hyperframe-worker:latest')}
    docker run -d --name hyperframe-worker --restart unless-stopped \\
      {' '.join(f'-e {k}' for k in env)} \\
      {os.getenv('DO_WORKER_IMAGE', 'registry.digitalocean.com/dronax-registry/hyperframe-worker:latest')}
"""


def get_fleet():
    if MODE == "digitalocean":
        return DigitalOceanFleet()
    return LocalDockerFleet()
