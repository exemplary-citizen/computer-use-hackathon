# product_availability (TypeScript)

The TypeScript counterpart to [`add_to_cart`](../add_to_cart/): drive the `hai-agents`
SDK from Node to find a product on a store's site and report its availability across
sizes, returning a typed, `zod`-validated answer. (This demo looks up the France
Jacquemus × Nike football jersey on jacquemus.com and checks sizes **S** and **XXL**.)

Entry point: [`src/index.ts`](src/index.ts).

## Run

```bash
cd examples/product_availability
npm install
# HAI_API_KEY must be in the environment or in examples/product_availability/.env
npm start
```

Set `HAI_REGION=us` to target the US endpoint (EU is the default). The script prints the
live `agent-view` link first, then a clear per-size availability report.
