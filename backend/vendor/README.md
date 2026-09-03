# backend/vendor/

Wheels for dependencies that are **not on PyPI**. The backend image installs
`vendor/*.whl` after `requirements.txt`.

Today that is exactly one package: **`yukta`**.

## Why a wheel and not a pip install

`yukta` is published only on an internal index / as a source checkout, so
`pip install yukta` inside a Dockerfile fails. A Docker build context also
cannot reach a checkout outside the repository, so the dependency has to exist
*inside* `backend/` before the image is built. A wheel is the smallest way to
do that without vendoring the whole source tree into version control.

## Building it

```bash
./scripts/build-yukta-wheel.sh            # auto-detects the checkout
./scripts/build-yukta-wheel.sh /path/to/yukta
```

## Why the build fails without it

All three integrated pipelines construct their agents through `yukta`. Without
it, **3 of the 4 modes come up dead**: Financial Statements, SAR report
*generation* (the entity/financial-year dropdowns keep working — they only need
`psycopg2` and `FINANCE_DSN`), and Trial Balance's `ask`/`audit`. Only TB
upload/list/delete/`validate` are unaffected, being pure Python against the
database.

Shipping that image silently would look like a working deployment and fail one
mode at a time in front of users, so `backend/Dockerfile` treats a missing
wheel as a build error. To build anyway — for a database-only smoke test, say:

```bash
ARTHA_ALLOW_MISSING_YUKTA=1 docker compose build backend
```

## Version control

The wheels themselves are git-ignored (binaries don't belong in the repo), and
this README is not. Anyone cloning fresh runs the script once before their
first build.
