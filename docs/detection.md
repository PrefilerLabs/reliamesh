# Local reliability detection

The detector is deterministic Python with no network calls or model inference.
It analyzes tenant-local reported outcomes. A stream is the exact combination of
deployment ID, agent ID, operation, and synthetic flag. Versions remain in the
same stream so a temporal change can be observed across a version rollout.

## Storage-independent callback contract

```python
from datetime import datetime, timezone
from reliamesh.detection import process_events, summarize
from reliamesh.protocol import Event

new_state, receipt = process_events(old_state, validated_events, datetime.now(timezone.utc))
# receipt == {"accepted": integer, "duplicates": integer}
summary = summarize(new_state, datetime.now(timezone.utc))
```

`old_state` is `{}` for a new tenant, otherwise the last returned JSON-compatible
dictionary. `validated_events` is a list of 1–100 `Event` objects. Both functions
require an aware `now` datetime. Neither mutates its input. The storage adapter
must atomically compare/update one tenant's state; transaction retries use the
same events and `now` value. `DetectionError.code` is a safe, static error code.
Any exception means no state change should be committed.

`summarize` returns `schema_version`, `totals`, `streams`, `incidents`, and `limits`.
Totals contain accepted and duplicate counters over the lifetime of the current
tenant state. Each stream contains its ID, dimensions, `status`, `warmup`,
`baseline`, `current`, active incident ID or null, version map and up to 16 current
failure fingerprints. Status is `warming`, `observed`, or `degraded`.
`observed` means sufficient observations with no active signal; it is not a
guarantee of health or a reliability score.

Window statistics expose counts, failures, rate and 95% Wilson interval, numeric
measurement sample counts and medians, first/last event timestamps (epoch seconds),
and version-ID counts. Missing medians/rates are null. `warmup` remains true until
both comparison windows have 50 events; an absolute failure incident may already
be open during warmup.

Incident records contain an ID, stream ID, synthetic flag, `status` (`open`,
`resolved`, `expired`), onset/resolution timestamps, resolution reason, signal
evidence, a frozen baseline summary and the most recent observation summary.
Incident history is capped at 32 entries, removing resolved/expired entries before
active entries. At most 16 active incidents can exist because streams are bounded.

## Windows, signals, recovery

The first 50 events establish a baseline and the next 50 form an observation
window. Subsequently both adjacent count windows roll one event at a time.
An active incident freezes the baseline while the observation window keeps rolling.
The event that crosses the threshold is the onset time, not a retrospective claim
about the first failed execution. These are event-count windows, not time buckets.

* **Failure-rate regression:** both windows contain 50 events; observed failure
  rate exceeds baseline by at least 15 percentage points, and the observed Wilson
  lower bound exceeds the baseline Wilson upper bound.
* **High absolute failure rate:** at least 25 observations and the 95% Wilson
  lower bound exceeds 50%. This catches continuously failing cold starts, where
  a failing baseline would otherwise normalize a problem.
* **Latency, token, or retry degradation:** both windows contain 50 events, with
  at least 40 known values in each. A threshold is the maximum of baseline p95,
  1.5 times baseline median, and baseline median plus a minimum meaningful change
  (50 ms, 100 tokens, or two retries). The current median must exceed the threshold,
  and the Wilson interval for the fraction of above-threshold observations must
  be entirely above the corresponding baseline interval.

The binomial intervals use the Wilson score formula with z = 1.959963984540054.
The method avoids the zero-width intervals produced by a naive normal approximation
at zero failures. See the [NIST statistical handbook](https://itl.nist.gov/div898/handbook/prc/section2/prc241.htm).

Recovery requires a full 50-event observation window, no currently firing signal,
and recovery for every signal accumulated in the incident. Failure-rate regression
must return within five percentage points of baseline; an absolute failure signal
must have an upper Wilson bound below 25%. Numeric signals require at least 40 known
values and a median no greater than the larger of 1.25 times baseline or baseline
plus 25 ms / 50 tokens / one retry. Missing telemetry cannot resolve an incident.
Recovered observations become the new baseline and a new observation window warms.

## Retention, ordering, and resource bounds

Samples, duplicate IDs, version labels without retained samples, and incident
history expire after seven days of event time. Both ingestion and summary reads
remove/suppress expired evidence. Expired baseline samples reset comparison
warmup and expire dependent open incidents. A quiet stream disappears after seven
days. Storage-level cleanup governs physical persistence; a summary read itself
does not rewrite the stored state. Aggregate accepted/duplicate totals are kept
while the tenant state remains active, with no event-level labels attached.

Per tenant: 16 streams, 50 baseline + 50 observation samples each, eight live
version sets per stream, 2,048 duplicate IDs, 32 incidents, and a hard 650,000-byte
JSON state guard. A request over these limits fails atomically. The guard may
be reached before all other maxima for unusually large metadata. Event time ordering
and bounded replay protection are specified in [the protocol](protocol.md).

## Limits of the evidence

These conservative thresholds are useful initial operating rules. Their repeated
overlapping-window checks are not a calibrated sequential hypothesis test and do
not establish a global 5% false-positive rate. Wilson intervals assume Bernoulli
sampling; correlated executions, biased instrumentation, missing events and changed
traffic mix can invalidate that interpretation. The detector has not been calibrated
on an independent production dataset. It does not provide causal attribution,
provider rankings, model quality judgments, or guaranteed lead time.

Versions are reported on both sides of a change; a version correlation is a lead
for investigation. Gradual changes may be absorbed by rolling baselines. Minor
regressions, low traffic and streams with fewer than 40 numeric measurements can
remain undetected. The ingestion pipeline does not independently classify semantic
output errors, loops, or task completion without local caller evidence.

This engine produces no cross-tenant/network incident. Independent contribution
verification and cohort suppression belong to a separate, explicitly configured
network mechanism. Synthetic incidents are labeled and cannot establish adoption
or ecosystem-wide reliability.
