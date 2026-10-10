# AI advisory report

Advisory only. Nothing here is executed: every experiment needs a human approver, and injection, rollback and recovery decisions stay with the deterministic safety controller.

## Proposed experiment order (provider: `rule-based`)

Fallback reason: AI disabled

Rule-based order (priority = 0.6 x information + 0.4 x risk).

1. **dependency_failure** on `influxDB` (rule-based rank 1)
   - 1 full-window run(s) so far (information 0.50); 3/4 expected symptoms still labelled HYPOTHESIS in config/experiments.json; 6/7 past runs unresolved (latched or cut short); 3 neighbouring container(s) in the dependency map: fms-consumer, fms-server, grafana; latch rate 2/7; mechanism docker_stop (severity 1.0); score so far: median 61.6 (range 61.6-61.6)
   - `rp run --type dependency_failure --target influxDB --duration 12 --approved-by <you>`
2. **service_crash** on `fms-forwarder` (rule-based rank 2)
   - 5 full-window run(s) so far (information 0.17); 3/3 expected symptoms still labelled HYPOTHESIS in config/experiments.json; 3/8 past runs unresolved (latched or cut short); 2 neighbouring container(s) in the dependency map: databroker, fms-zenoh-router; latch rate 3/8; mechanism docker_stop (severity 0.7); score so far: median 65.1 (range 61.9-65.3)
   - `rp run --type service_crash --target fms-forwarder --duration 12 --approved-by <you>`
3. **telemetry_stall** on `fms-forwarder` (rule-based rank 3)
   - 2 full-window run(s) so far (information 0.33); 0/3 expected symptoms still labelled HYPOTHESIS in config/experiments.json; 0/2 past runs unresolved (latched or cut short); 2 neighbouring container(s) in the dependency map: databroker, fms-zenoh-router; latch rate 0/2; mechanism docker_pause (severity 0.3); score so far: median 62.2 (range 60.9-63.5)
   - `rp run --type telemetry_stall --target fms-forwarder --duration 12 --approved-by <you>`

## Root causes and remediation (provider: `rule-based`)

Fallback reason: AI disabled

### F1. not_recovered — dependency_failure (confidence: medium, source: rule-based)

Recovery criteria were not met within 30 s because the consumer was still failing name resolution after InfluxDB returned (verified log line); the DNS-cache mechanism is a hypothesis.

Evidence: `20261010T192839Z-179795`

Playbook:
1. [verify] Re-run dependency_failure and record time from InfluxDB healthy to the consumer's first successful write.
2. [mitigate] Restart fms-consumer automatically when writes fail for longer than N seconds.
3. [fix] Make the consumer re-resolve and retry with backoff instead of reusing a failed lookup.

### F2. silent_after_rollback — dependency_failure (confidence: medium, source: rule-based)

After InfluxDB returned, fms-consumer kept failing to resolve its host name for ~27 s (logged, verified), so it wrote nothing while fms-server kept answering 200 with frozen data. Candidate mechanism: failed-DNS caching in the isahc/libcurl client (hypothesis, not verified).

Evidence: `20261010T192839Z-179795`

Playbook:
1. [verify] Correlate `docker logs fms-consumer` 'failed to resolve host name' timestamps with InfluxDB healthy time.
2. [mitigate] Gate readiness on fresh createdDateTime, not on HTTP 200 or container running.
3. [fix] Disable or shorten the HTTP client's DNS cache (e.g. CURLOPT_DNS_CACHE_TIMEOUT) or re-resolve on connect errors.

### F3. silent_during_fault — service_crash (confidence: high, source: rule-based)

fms-server serves the last stored status with receivedDateTime = request time and no staleness signal, so the API stays HTTP 200 while vehicle data is frozen (verified).

Evidence: `20261010T181441Z-920732`, `20261010T181503Z-31eafe`, `20261010T181525Z-8fa234`, `20261010T182331Z-b89ae0`, `20261010T183644Z-8941cc`

Playbook:
1. [verify] Compare createdDateTime age with the 5 s threshold on every poll (`rp observe`).
2. [mitigate] Alert on createdDateTime age > 5 s instead of on HTTP status or container state.
3. [fix] Expose data age in the rFMS response or return 503 when the newest status is older than a threshold.
4. [fix] Handle SIGTERM in fms-forwarder for a graceful shutdown (today docker stop always ends in SIGKILL, exit 137; verified).

### F4. silent_during_fault — telemetry_stall (confidence: high, source: rule-based)

fms-server serves the last stored status with receivedDateTime = request time and no staleness signal, so the API stays HTTP 200 while vehicle data is frozen (verified).

Evidence: `20261010T181417Z-c1ab0d`, `20261010T183702Z-21c920`

Playbook:
1. [verify] Compare createdDateTime age with the 5 s threshold on every poll (`rp observe`).
2. [mitigate] Alert on createdDateTime age > 5 s instead of on HTTP status or container state.
3. [fix] Expose data age in the rFMS response or return 503 when the newest status is older than a threshold.

### F5. slow_recovery — dependency_failure (confidence: medium, source: rule-based)

fms-server answered HTTP 500 ('failed to resolve host name') for ~22 s after InfluxDB was back (verified), so recovery took far longer than InfluxDB's own restart. DNS caching is the candidate cause (hypothesis).

Evidence: `20261010T193003Z-e2eacd`

Playbook:
1. [verify] Time InfluxDB healthy -> first fms-server HTTP 200 across 3 runs; check logs for resolve errors.
2. [mitigate] Add a readiness probe on fms-server that runs a real query, and retry clients with backoff.
3. [fix] Configure the HTTP client to re-resolve host names after connection failures.

### F6. data_loss — dependency_failure (confidence: high, source: rule-based)

fms-consumer drops statuses it cannot write ('failed to write data to influx'); there is no buffer or backfill (verified). Loss happens in the fast-fail phase; writes during the blocking phase are delayed, not dropped.

Evidence: `20261010T185355Z-540224`, `20261010T185440Z-2f33aa`, `20261010T185448Z-595a16`, `20261010T191733Z-7123d9`, `20261010T193003Z-e2eacd`

Playbook:
1. [verify] Query stored statuses for the outage window (`data_gap` in the run record).
2. [mitigate] Alert on consumer write failures.
3. [fix] Add a bounded in-memory/disk write buffer with retry in fms-consumer, or a Zenoh storage for replay.

### F7. blast_radius — service_crash (confidence: low, source: rule-based)

A container outside the experiment's blast radius changed state. Check the run annotation first: the Day-2 occurrence was a deliberate guard test (operator restarted Grafana).

Evidence: `20261010T182500Z-4aa9f7`

Playbook:
1. [verify] Inspect `docker events` for the run window and the run's annotation.
2. [mitigate] Keep the blast-radius guard armed; it latched the run as designed.
3. [fix] If not operator-caused, trace the dependency that propagated the fault.

