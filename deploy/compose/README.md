# Reference Deployment

## Prerequisites

- Docker and Docker Compose
- (Optional) Tradier sandbox credentials

## Setup

1. Place your input files in `data/`:
   - `policy.yaml` — your capital policy
   - `intents.jsonl` — order intent stream (one JSON object per line)
   - `portfolio.json` — initial portfolio state
   - `market.json` — market snapshot

2. Run with sim broker (no credentials needed):
   ```bash
   docker compose up --build
   ```

3. Outputs are in the `audit-logs` volume:
   ```bash
   docker compose run --rm policygate-run cat /output/audit.jsonl
   docker compose run --rm policygate-run cat /output/summary.json
   ```

## Tradier Mode

1. Create a `.env` file:
   ```
   TRADIER_TOKEN=your-token
   TRADIER_ACCOUNT_ID=your-account-id
   TRADIER_ENV=sandbox
   ```

2. Edit `docker-compose.yml` and change `--broker=sim` to `--broker=tradier`.

3. Run:
   ```bash
   docker compose up --build
   ```
