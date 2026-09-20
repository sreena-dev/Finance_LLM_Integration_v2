"""Live upload for the Financial Statement mode.

An uploaded statement is converted by the ingestion service and served to the
*existing* FS tool library through `bridge.install()`.

**The uploaded FILE is never stored** -- not on disk, not in Redis, not in
Postgres. `ingestion/app/jobs.py` drops the PDF's bytes the moment the
conversion job ends, and nothing downstream ever sees them again.

The **extraction** does persist: Postgres holds it for
`ARTHA_FS_UPLOAD_RETENTION_DAYS` (30) as the system of record, and Redis caches
it for the life of the conversation. That split exists because the extraction
used to live only in Redis on a 2-hour TTL, and when it lapsed there was no way
back -- the source PDF was already gone, so the user had to re-upload and pay
for a full re-conversion. See `schema.py` for what is stored and why Redis
alone could not hold it for weeks.
"""
