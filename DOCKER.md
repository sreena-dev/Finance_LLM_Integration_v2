# Running Artha.AI with Docker

Two containers: the FastAPI gateway and an nginx that serves the built SPA and
reverse-proxies `/api` to the gateway. Phoenix tracing is a third, opt-in.

```bash
cp .env.example .env                  # then fill in credentials
./scripts/build-yukta-wheel.sh        # once — yukta is not on PyPI
docker compose up -d --build
```

Open **http://localhost:12100**.

---

## Ports — the 121xx series

| Port | Service | Notes |
|---|---|---|
| **12100** | frontend (nginx) | the only port a user needs |
| **12101** | backend (uvicorn) | also load-bearing internally, see *Networking* |
| 12103 | Vite dev server | only when running the UI outside Docker |
| 12104 | `vite preview` | only when running the UI outside Docker |
| 12106 | Phoenix UI | `--profile tracing` only |
| 12107 | Phoenix OTLP ingest | `--profile tracing` only |

One contiguous reserved block, not conventional defaults. This host already
runs a dozen other compose projects across 8000–11999 — 8080, 8090, 8600, 5173,
6006 and 4317 are all taken — and because the API port is a *proxy target*, a
collision shows up as a puzzling 404 from someone else's service rather than as
a connection error. Every port is set in `.env`; change them there, not here.

---

## Configuration

**One file: `.env` at the repo root.** Three consumers read it:

- **the gateway** — `app/main.py` loads it before importing any mode
- **Docker Compose** — feeds it to the backend container via `env_file`, and
  reads the `ARTHA_*` names to build the stack itself
- **`vite.config.js`** — for the dev server's ports and proxy target

Credentials and pipeline endpoints keep the names the vendored pipelines already
expect (`FINANCE_DSN`, `EMBEDDING_URL`, …). Everything added for deployment is
prefixed **`ARTHA_`** so the two groups never get confused.

`.env.example` is the same file with credentials blanked — regenerate it if you
add a variable. `backend/.env.example` remains for the bare-metal/venv route
documented in the main README; the root file is canonical for Docker.

Nothing is baked into an image: config arrives at container start, so changing
an endpoint is `docker compose up -d`, not a rebuild.

---

## Timezone

`TZ=Asia/Kolkata`, applied in **both** images (`tzdata` + `/etc/localtime`) and
exported as `TZ`. Verified: `date` and Python's `datetime.now()` inside the
containers both report IST.

This is correctness, not cosmetics — the pipelines derive financial-year
boundaries from "now", and on a UTC container every timestamp after 18:30 IST
falls on the previous date.

---

## The `yukta` wheel

`yukta` is not on PyPI, so `pip install yukta` cannot work inside a Dockerfile,
and a build context cannot reach a checkout outside the repo. It is supplied as
a wheel in `backend/vendor/`:

```bash
./scripts/build-yukta-wheel.sh                  # auto-detects the checkout
./scripts/build-yukta-wheel.sh /path/to/yukta
```

Without it, **3 of the 4 modes are dead** — Financial Statements, SAR report
*generation* (the dropdowns still work; they need only `psycopg2` +
`FINANCE_DSN`) and Trial Balance `ask`/`audit`. Shipping that quietly would look
like a working deployment and fail one mode at a time in front of users, so a
missing wheel **fails the build**. To build anyway, for a DB-only smoke test:

```bash
ARTHA_ALLOW_MISSING_YUKTA=1 docker compose build backend
```

---

## Networking

Both services sit on Docker's **existing default bridge** and allocate no new
subnet. That is deliberate: on this host every automatic pool is already carved
up across ~34 networks, and the one free block in Docker's `192.168.0.0/16`
pool spans `192.168.200.x` — this machine's own LAN, and the address of every
Postgres, embedding and reranker server the pipelines call. A network there
would have silently black-holed all of them, presenting as intermittent
database timeouts rather than as a routing mistake.

The consequence is that there is no container-name DNS, so nginx reaches the
gateway at `host.docker.internal:12101` (mapped to the docker0 gateway by the
frontend's `extra_hosts`) — i.e. **through the backend's published port**.

> **`ARTHA_BIND_HOST` must stay reachable from containers.** `0.0.0.0` (the
> default) is fine; `127.0.0.1` would break the frontend's `/api` proxy. To keep
> the API off the LAN, firewall port 12101 rather than narrowing the bind
> address.

Switching back to a dedicated network later is a small edit: replace both
`network_mode: bridge` lines with `networks: [artha]`, add a top-level
`networks:` block with a subnet clear of `192.168.200.0/24` and `10.10.0.0/16`,
drop the frontend's `extra_hosts`, and set `ARTHA_BACKEND_HOST=backend`.

---

## Production hardening

| | |
|---|---|
| **Non-root** | backend uid 10001, nginx uid 101 |
| **Read-only rootfs** | both containers, with narrow tmpfs mounts |
| **`no-new-privileges`, `cap_drop: ALL`** | both containers |
| **Multi-stage builds** | no compilers or npm in the shipped images |
| **`npm ci`** | lockfile is authoritative; no floating transitive deps |
| **Healthchecks** | both, with `depends_on: service_healthy` gating the frontend |
| **Log rotation** | 20 MB × 5 per container |
| **Resource limits** | CPU and memory, per service |
| **No secrets in layers** | `.env` is `.dockerignore`d and injected at runtime |

A read-only rootfs is safe because **this project's own code writes nothing to
disk**: uploads stream into Postgres, and the audit workbook is assembled in an
in-memory buffer. Any accidental write becomes a loud failure instead of state
that vanishes on the next deploy.

Its **dependencies** are another matter, so the backend gets two explicit
writable paths:

| Path | Backing | Why |
|---|---|---|
| `/var/lib/artha` | `artha-data` volume | `YUKTA_DATA_DIR`. `yukta` creates a `JSONFileStorage` for agent chat sessions, defaulting to `~/.yukta`. |
| `/home/artha` | tmpfs, uid 10001 | `HOME`. Scratch, for any library reaching after `~/.cache` or `~/.config`. |

This is worth understanding rather than copying, because of how the failure
presents. `yukta`'s storage backend calls `mkdir` **in its constructor**, before
any agent runs. Left at its `~/.yukta` default with `--no-create-home` and a
read-only rootfs, the first tool-calling request in any LLM-backed mode dies
with:

```
Pipeline error at stage 'llm': The agent's tool-calling run failed
([Errno 30] Read-only file system: '/home/artha')
```

The UI reports that as an LLM/pipeline error — mode health still shows green,
because probing never constructs the agent. Nothing hints at a mount. Setting
`YUKTA_DATA_DIR` to a volume-backed path is what fixes it; `HOME` existing is
the belt-and-braces for the next dependency that does the same thing.

The volume normally stays near-empty (the directory has to *exist*; whether
sessions land in it depends on the mode). It is a named volume rather than a
tmpfs because agent transcripts are cheap to keep and awkward to discover
missing after the fact.

### Two things worth knowing before you edit the nginx config

- **`/etc/nginx/conf.d` is a tmpfs, owned by uid/gid 101.** The base image
  renders the config template through `envsubst` into that directory at every
  start, so it must be writable — and a tmpfs mount defaults to `root:root`,
  which is why the `uid`/`gid` options are there. Without them nginx starts with
  no server block at all and the port resets every connection. Static config
  files therefore live in `/etc/nginx/snippets/`, outside the mount.
- **`add_header` in a location replaces the inherited set, it does not add to
  it.** Any location defining its own `add_header` must also
  `include /etc/nginx/snippets/security-headers.conf`, or it silently serves no
  security headers. `/` is exactly such a location, since `try_files` rewrites
  it internally to `/index.html`.

### Why one worker

`ARTHA_BACKEND_WORKERS=1` is a correctness requirement, not a placeholder.
Trial Balance's `ask` mode keeps conversation memory in an **in-process**
`SessionStore`, and each mode's pipeline is imported lazily into whichever
worker first serves it. A second worker would scatter follow-up questions
across processes that do not share that memory, and duplicate every pipeline's
resident footprint. **Scale with replicas behind a proxy, not with workers.**

Trial Balance's column-mapper hand-offs used to share that fragility — they were
a module-level dict too — but now go through `modes/trial_balance/token_store.py`,
which writes them under `PREVIEW_CACHE_DIR` (default: the `/tmp` tmpfs) so they
survive across worker processes. That makes the mapper safe at any worker count;
`ask`'s memory is what still pins this to one. Note that replicas do **not**
share a `/tmp`: if you run more than one backend replica, point
`PREVIEW_CACHE_DIR` at a shared volume, or a user who lands on a different
replica mid-mapping sees "preview token not found or expired".

### Timeouts

30 minutes end to end — nginx (`ARTHA_PROXY_TIMEOUT`) and the Vite dev proxy
(`ARTHA_PROXY_TIMEOUT_MS`) use the same value, so a request that survives dev
does not 504 only once deployed. SAR generation runs 4 LLM calls; the Trial
Balance single-TB pipeline runs ~20 sequential tool calls, several of them their
own LLM reasoning steps. nginx's 60 s default would cut a report off mid-write
and look like a backend crash.

`ARTHA_MAX_UPLOAD_SIZE=64m` for the same class of reason: real-world Excel
trial-balance exports exceed nginx's 1 MB default, which would reject them with
a 413 before FastAPI ever saw the request.

---

## Operating it

```bash
docker compose ps                       # status + health
docker compose logs -f backend          # follow gateway logs
docker compose restart backend          # pick up an .env change
docker compose up -d --build            # rebuild after a code change
docker compose down                     # stop (Phoenix traces survive)

# Are the modes actually ready? (imports every pipeline, opens DB connections)
curl -s 'http://localhost:12100/api/modes?probe=true' | python3 -m json.tool
```

### Tracing

```bash
docker compose --profile tracing up -d
```

Then in `.env` set `ENABLE_TRACING=true` and
`PHOENIX_ENDPOINT=http://host.docker.internal:12107/v1/traces`, and restart the
backend. UI at http://localhost:12106. Traces persist in the
`artha-phoenix-data` volume — the one piece of state in this stack worth keeping
across restarts.

### Troubleshooting

| Symptom | Cause |
|---|---|
| Build fails: `no wheel found in backend/vendor/` | Run `./scripts/build-yukta-wheel.sh`. |
| UI loads, every API call fails | The gateway must be reachable at `ARTHA_BIND_HOST:12101` from inside a container — check it is not bound to `127.0.0.1`. |
| Frontend restarts in a loop | `/etc/nginx/conf.d` tmpfs is not writable by uid 101; check its `uid=101,gid=101` options. |
| A mode reports itself unavailable | Ask it why: `curl -s localhost:12100/api/<mode-id>/health`. The `reason` field names the exact missing variable. |
| Mode health is green but every query fails with `[Errno 30] Read-only file system` | A dependency is writing outside the two writable paths. Find where (`docker exec artha-backend python -c 'import pathlib;print(pathlib.Path.home())'`), then either redirect it via its own env var — as `YUKTA_DATA_DIR` does — or add a tmpfs. Do not drop `read_only`. |
| `all predefined address pools have been fully subnetted` | Something reintroduced a custom network. See *Networking*. |
