# AI advisory report

Advisory only. Nothing here is executed: every experiment needs a human approver, and injection, rollback and recovery decisions stay with the deterministic safety controller.

## Proposed experiment order (provider: `ollama:qwen2.5:7b`)

Reordered experiments based on risk and informativeness, prioritizing the most risky and informative first.

1. **dependency_failure** on `influxDB` (rule-based rank 1)
   - This experiment has the highest risk due to multiple unresolved runs, high latch rate, and critical dependencies. It also has the highest priority score and evidence of high severity issues.
   - `rp run --type dependency_failure --target influxDB --duration 12 --approved-by <you>`
2. **service_crash** on `fms-forwarder` (rule-based rank 2)
   - This experiment has a high number of full-window runs and unresolved issues, indicating significant risk. It also has a high priority score and critical dependencies.
   - `rp run --type service_crash --target fms-forwarder --duration 12 --approved-by <you>`
3. **telemetry_stall** on `fms-forwarder` (rule-based rank 3)
   - While this experiment has the least unresolved issues, it still provides valuable information about the system's behavior under stress. It has a lower priority score but is still informative.
   - `rp run --type telemetry_stall --target fms-forwarder --duration 12 --approved-by <you>`

## Root causes and remediation (provider: `ollama:qwen2.5:7b`)

### F1. not_recovered — dependency_failure (confidence: medium, source: rule-based)

Recovery criteria were not met within 30 s after InfluxDB returned (verified from the run record). Hypothesis: the consumer was still failing name resolution (log excerpts not committed); the DNS-cache mechanism is a further hypothesis.

_AI answer rejected: states DNS caching as fact; it is an unverified hypothesis. The model said: "The HTTP client (isahc/libcurl) caches failed DNS lookups, delaying recovery after InfluxDB restarts."_

Evidence: `20261010T192839Z-179795`

Playbook:
1. [verify] Re-run dependency_failure and record time from InfluxDB healthy to the consumer's first successful write.
2. [mitigate] Restart fms-consumer automatically when writes fail for longer than N seconds.
3. [fix] Make the consumer re-resolve and retry with backoff instead of reusing a failed lookup.

### F2. silent_after_rollback — dependency_failure (confidence: medium, source: rule-based)

After InfluxDB returned, the API kept answering 200 with frozen data for ~21-24 s (verified from run records). Hypothesis: fms-consumer kept failing to resolve InfluxDB's host name (reported from container logs, excerpts not committed), so it wrote nothing. Candidate mechanism: failed-DNS caching in the isahc/libcurl client (hypothesis, not verified).

_AI answer rejected: does not name the component the evidence points to (one of ['consumer']). The model said: "The HTTP client (isahc/libcurl) caches failed DNS lookups, delaying recovery after InfluxDB restarts."_

Evidence: `20261010T192839Z-179795`, `20261010T203817Z-01d396`

Playbook:
1. [verify] Correlate `docker logs fms-consumer` 'failed to resolve host name' timestamps with InfluxDB healthy time.
2. [mitigate] Gate readiness on fresh createdDateTime, not on HTTP 200 or container running.
3. [fix] Disable or shorten the HTTP client's DNS cache (e.g. CURLOPT_DNS_CACHE_TIMEOUT) or re-resolve on connect errors.

### F3. silent_during_fault — service_crash (confidence: high, source: rule-based)

fms-server serves the last stored status with receivedDateTime = request time and no staleness signal, so the API stays HTTP 200 while vehicle data is frozen (verified).

_AI answer rejected: does not name the component the evidence points to (one of ['fms-server', 'stored status', 'last status', 'createdDateTime']). The model said: "The fms-forwarder ignores SIGTERM and continues running, causing stale data to be served."_

Evidence: `20261010T181441Z-920732`, `20261010T181503Z-31eafe`, `20261010T181525Z-8fa234`, `20261010T182331Z-b89ae0`, `20261010T183644Z-8941cc`

Playbook:
1. [verify] Compare createdDateTime age with the 5 s threshold on every poll (`rp observe`).
2. [mitigate] Alert on createdDateTime age > 5 s instead of on HTTP status or container state.
3. [fix] Expose data age in the rFMS response or return 503 when the newest status is older than a threshold.
4. [fix] Handle SIGTERM in fms-forwarder for a graceful shutdown (today docker stop always ends in SIGKILL, exit 137; verified).

### F4. silent_during_fault — telemetry_stall (confidence: high, source: rule-based)

fms-server serves the last stored status with receivedDateTime = request time and no staleness signal, so the API stays HTTP 200 while vehicle data is frozen (verified).

_AI answer rejected: does not name the component the evidence points to (one of ['fms-server', 'stored status', 'last status', 'createdDateTime']). The model said: "The fms-forwarder ignores SIGTERM and continues running, causing stale data to be served."_

Evidence: `20261010T181417Z-c1ab0d`, `20261010T183702Z-21c920`

Playbook:
1. [verify] Compare createdDateTime age with the 5 s threshold on every poll (`rp observe`).
2. [mitigate] Alert on createdDateTime age > 5 s instead of on HTTP status or container state.
3. [fix] Expose data age in the rFMS response or return 503 when the newest status is older than a threshold.

### F5. slow_recovery — dependency_failure (confidence: medium, source: rule-based)

fms-server answered HTTP 500 for ~18-22 s after InfluxDB was back (verified from run records), so recovery took far longer than InfluxDB's own restart. Suspected cause (hypothesis): name-resolution failures reported from container logs (excerpts not committed), possibly from DNS caching.

_AI answer rejected: states DNS caching as fact; it is an unverified hypothesis. The model said: "The HTTP client (isahc/libcurl) caches failed DNS lookups, delaying recovery after InfluxDB restarts."_

Evidence: `20261010T193003Z-e2eacd`, `20261010T203712Z-08b9dd`, `20261010T203817Z-01d396`, `20261010T211735Z-748875`

Playbook:
1. [verify] Time InfluxDB healthy -> first fms-server HTTP 200 across 3 runs; check logs for resolve errors.
2. [mitigate] Add a readiness probe on fms-server that runs a real query, and retry clients with backoff.
3. [fix] Configure the HTTP client to re-resolve host names after connection failures.

### F6. data_loss — dependency_failure (confidence: high, source: ollama:qwen2.5:7b)

The fms-consumer drops statuses it cannot write ('failed to write data to influx'); no buffering or backfill.

Evidence: `20261010T185355Z-540224`, `20261010T185440Z-2f33aa`, `20261010T185448Z-595a16`, `20261010T191733Z-7123d9`, `20261010T193003Z-e2eacd`, `20261010T203616Z-596e45`, `20261010T203712Z-08b9dd`, `20261010T203817Z-01d396`, `20261010T211735Z-748875`

Playbook:
1. [verify] Check the data handling and buffering mechanisms of the fms-consumer.
2. [mitigate] Implement a backfill mechanism to recover lost data.
3. [fix] Update the fms-consumer to buffer data and retry writes, or implement a backfill strategy.

### F7. blast_radius — service_crash (confidence: low, source: rule-based)

A container outside the experiment's blast radius changed state. Check the run annotation first: the Day-2 occurrence was a deliberate guard test (operator restarted Grafana).

_AI answer rejected: ignores the run annotation (deliberate guard test: operator ran `docker restart grafana` during the fault). The model said: "The fms-forwarder ignores SIGTERM and continues running, causing stale data to be served."_

Evidence: `20261010T182500Z-4aa9f7`

Playbook:
1. [verify] Inspect `docker events` for the run window and the run's annotation.
2. [mitigate] Keep the blast-radius guard armed; it latched the run as designed.
3. [fix] If not operator-caused, trace the dependency that propagated the fault.


Model calls (prompts, raw responses, validation): `20261010T233726Z.json` in the evidence store's ai_calls/.
