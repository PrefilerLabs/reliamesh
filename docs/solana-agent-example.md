# Solana Devnet RPC readiness example

[`examples/solana_rpc_agent.py`](../examples/solana_rpc_agent.py) shows a deterministic planning agent checking its read-only RPC tool before continuing a plan. It helps an integrator distinguish an unavailable RPC, malformed output, the wrong cluster, and a blockhash approaching expiry. The example executes no planned action, uses no wallet, requests no airdrop, and submits no transaction. It uses the Python standard library and ReliaMesh SDK; no Solana SDK dependency is required.

This is an integration example, not evidence of adoption or independent production reliability. It does not use an LLM. It complements optional report attestations by showing where classified reliability observations originate in an actual Solana tool workflow.

## Run without collection

From a source checkout with Python 3.12 or later:

```sh
python -m pip install -e sdk/python
python examples/solana_rpc_agent.py --fixture
```

No arguments also selects fixture mode. It performs 200 local checks: 50 healthy, 50 injected RPC errors, then 100 healthy. Expected output includes `synthetic: true`, `ready: 150`, `deferred: 50`, and `collection_enabled: false`. The outcomes are deterministic; event IDs, deployment IDs, and timestamps are fresh on each run. All synthetic events use a fixed illustrative latency of 1 ms. There are no network requests, and no claim that an incident was detected when collection is disabled.

To perform one real read-only check against the official Devnet endpoint:

```sh
python examples/solana_rpc_agent.py --live
```

The endpoint is fixed to `https://api.devnet.solana.com`; there is no custom RPC override or ambient Solana configuration. The check performs at most four requests, stopping on the first failure:

1. `getGenesisHash` must match the pinned Devnet genesis.
2. `getSlot` establishes a confirmed context slot.
3. `getLatestBlockhash` requests at least that context slot and validates the returned blockhash and last valid block height.
4. `getBlockHeight` uses the latest context slot. The returned height must leave at least 20 blocks before expiry, an illustrative local policy.

The RPC methods return cluster identity and block validity information; `minContextSlot` prevents accepting an earlier context in subsequent reads. [Genesis hash](https://solana.com/docs/rpc/http/getgenesishash), [latest blockhash](https://solana.com/docs/rpc/http/getlatestblockhash), [block height](https://solana.com/docs/rpc/http/getblockheight).

Passing means these limited checks passed at observation time. It does not prove absolute freshness, global consensus health, account state, transaction safety, or future inclusion. A consistently stale RPC can pass this relative consistency check. Block height and slot are separate quantities. Recheck relevant state when constructing any real action in your application. Devnet can reset and its public endpoint is rate-limited; a genesis mismatch requires a reviewed pin update, never an automatic fallback. [Official cluster documentation](https://solana.com/docs/references/clusters)

## Explicit collection and regression evidence

Collection requires both `--endpoint` and `RELIAMESH_API_KEY`. An environment key alone does nothing. The credential needs ingest and read scopes for the fixture's assertions. Use a disposable tenant with enough quota and room for one new stream; avoid other writers while checking accepted-count deltas.

Set `RELIAMESH_API_KEY` in the process environment using your normal secret-handling mechanism, then run:

```sh
python examples/solana_rpc_agent.py --fixture --endpoint http://127.0.0.1:8080 --summary-output .local/solana-fixture.json
```

The directory must already exist, and the output file must not exist. `--summary-output` is optional and only available for fixtures. The command verifies a real API regression after the first 100 observations, then verifies the same incident resolved after 100 more successes. It requires 200 newly accepted events, zero duplicates, zero SDK drops, and an empty queue. An ingestion or assertion failure exits with a sanitized error rather than reporting success. The 50 failures are injected fixtures, not a Devnet outage.

`run_fixture(client=None)` is also available for Python callers. Supply a fresh `reliamesh_sdk.Client` for collection. Its result contains:

- `deployment_id`, `synthetic`, `checks`, `ready`, `deferred`, and `collection_enabled`.
- `regression_summary` and `recovery_summary`, each restricted to this generated synthetic stream and selected incident/count fields.
- `evidence` with accepted, duplicate, sent, dropped, queued, and incident assertion results.

Without a client, both summaries and evidence are `None`. Selected summaries can feed the server's `reliamesh.attestation.report_from_summary` for a separate count-only report. The example does not publish or attest anything. Keep intermediate evidence local; creating a report or commitment is a separate explicit action.

## Classification, privacy, and bounds

Each complete readiness-tool invocation emits at most one schema-1.0 event with `operation: tool`. It explicitly classifies semantic failures because an HTTP 200 alone does not establish success. Transport/HTTP/JSON-RPC errors map to `tool_error`; timeouts to `timeout`; invalid response structure to `malformed_output`; wrong cluster, regressed context, or insufficient block validity to `validation_failure`. Known fixed component/version labels describe the example. Fixture agent versions identify simulated healthy/error phases, not changes to Solana software.

Events contain outcome, classification, latency, synthetic status, random deployment identity, and fixed component/version labels. RPC response bodies, RPC error messages, blockhashes, slot/height values, account addresses, prompts, and credentials are not copied into telemetry or stdout. Extra RPC data is ignored. The output file projects only the generated stream; it does not dump the tenant summary.

The live RPC client uses TLS, bypasses proxy environment variables, follows no redirects, retries nothing, allows only four read methods, and caps each response at 16 KiB. A socket operation times out after three seconds; response-body reading also checks an elapsed-time budget between reads. This is not a hard process deadline: operating-system DNS resolution and HTTP header handling have their own behavior. Run blocking checks outside an application's asynchronous event loop. The SDK queues observations locally and performs synchronous delivery only at explicit `flush()` calls; collection uses a three-second timeout and at most one retry for 429/503.

Live exit status is 0 for passed checks, 2 for deferred planning, and 1 for runtime collection/configuration/output failure. Invalid CLI arguments or a missing required credential also use status 2. Fixture status 0 means its applicable assertions passed, including server detection only when collection was explicitly enabled. This example is bounded instrumentation, not a transaction authorization or execution system.
