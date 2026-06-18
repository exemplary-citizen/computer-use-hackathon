# add_to_cart (Python)

Drive an H Computer-Use agent straight from the SDK: search a shopping site for a
product and add it to the cart. The agent *sees* the page and clicks through the UI;
there's no store API involved. (This demo searches for Daft Punk's "Random Access
Memories".)

Single file: [`add_to_cart.py`](add_to_cart.py).

## Run

```bash
# from the repo root, with HAI_API_KEY in your environment (or .env)
python3 -m venv .hai-venv && .hai-venv/bin/pip install -q hai-agents
source .env && .hai-venv/bin/python examples/add_to_cart/add_to_cart.py
```

The script prints the live `agent-view` link up front so you can watch the run, then
reports the product title, format, price, and whether it was added to the cart. It
never proceeds to checkout.
