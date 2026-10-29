# Optional Local GCP Emulation with Floci (BigQuery & Cloud Run)

> [!NOTE]
> By default, this repository runs in **zero-Docker local mode (`BQ_MODE=local`)** using Python's built-in `sqlite3` analytical engine so you can run all tests immediately without downloading container images. This guide documents how to optionally connect the project to **Floci (`floci-gcp`)** or real **Google Cloud Platform (GCP)**.

---

## 1. Architecture Emulated

`Cloud Scheduler -> Cloud Run -> BigQuery`

- **Floci (`floci/floci-gcp:latest`)** exposes emulated GCP APIs on port `4588`.
- Typical Docker image download size: ~250–500 MB compressed (~800 MB–1.2 GB uncompressed).

---

## 2. Starting the Floci Emulator (Optional)

If you have Docker Desktop running and wish to test against the container emulator:

```bash
docker run -d \
  --name floci-gcp \
  -p 4588:4588 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  floci/floci-gcp:latest
```

Verify the container is running:

```bash
docker ps --filter "name=floci-gcp"
```

---

## 3. Connecting `HybridBigQueryStorage` to Floci or Real GCP

1. Install the optional official BigQuery SDK:
   ```bash
   .venv/bin/pip install google-cloud-bigquery
   ```
2. Set `BQ_MODE=floci` (or `BQ_MODE=gcp` for a real GCP project):
   ```bash
   export BQ_MODE=floci
   export FLOCI_ENDPOINT="http://localhost:4588"
   ```

When `BQ_MODE=floci` is active, [`HybridBigQueryStorage`](../src/bq_storage.py) initializes `bigquery.Client` with `ClientOptions(api_endpoint="http://localhost:4588")`.

---

## 4. Cleanup

```bash
docker stop floci-gcp && docker rm floci-gcp
```

