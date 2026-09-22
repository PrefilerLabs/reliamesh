# Cost controls

ReliaMesh uses only the authorized GCP project `reliamesh`. There are no paid
external services, AI inference dependencies, subscriptions, or billing features.

Cloud Run scales to zero with service and revision caps of two instances. Every
tenant has an atomic limit of 10,000 accepted events per UTC day. Batches amortize
Firestore transactions; tiny batches cost more per event. The API also bounds
requests, payload bytes, state size, keys, streams, history, and retries.

Cloud Run maximum instances are a capacity control, not a guaranteed spending
ceiling. Platform requests, outbound traffic, storage, TTL deletes, PITR,
monitoring, build artifacts and attack traffic can incur charges. Network abuse
requires operational response even when application quotas hold.

Firestore is regional Standard Native with bounded per-tenant state, index
exemptions for serialized state, asynchronous TTL and seven-day recovery history.
Logging retention is seven days. Artifact Registry removes untagged images after
seven days while retaining the ten latest images. Old tagged releases require
deliberate cleanup after verifying that no active revision uses them.

Custom-domain production hosting uses a GCP HTTPS load balancer when provisioned.
Its forwarding rule has a fixed hourly charge (currently US$0.025/hour, roughly
US$18.25 for 730 hours), plus applicable processing/network/IP charges and backend
usage. See [official pricing](https://cloud.google.com/load-balancing/pricing).
No cost estimate is a promise of a free service or a hard cap.

Project-scoped monitoring can alert on backend request/error rates and resource
usage. Billing-account configuration is outside this project's authorization;
no billing account IAM or budget was changed. Review actual project costs in the
billing console and reduce quotas or disable public ingress if abuse occurs.

