# Grocy Helper for Home Assistant

> **⚠ Early development — use at your own risk.**
> This project is under active development. Breaking changes are imminent. APIs, configuration schemas, and queue behavior may change between versions without migration paths.

**Manage your entire Grocy inventory from Home Assistant — by scanning barcodes.**

Grocy Helper bridges [Grocy](https://grocy.info) and [Home Assistant](https://www.home-assistant.io) into a single, streamlined barcode-scanning workflow. Scan a product, and the integration figures out the rest: look up or create the product, fill in sensible defaults, and record the transaction in Grocy — all from a Home Assistant menu.

## Why Grocy Helper?

Keeping Grocy up to date is tedious. Every purchase, every consumption, every spoiled item requires opening the Grocy UI, finding the product, and filling in a form. Grocy Helper removes that friction:

- **Scan instead of type.** Point any barcode scanner (hardware or app) at a product and the integration takes it from there.
- **Works while you're away.** A webhook endpoint lets you queue scans remotely — from an NFC tag on your fridge, a phone shortcut, or any automation. Known products are resolved automatically; everything else waits for you.
- **Handles the edge cases.** Unknown product? The integration walks you through creating it. Need to produce a recipe, transfer stock, or print a label? There's a step for that.
- **Stays out of your way.** Sensible defaults mean most scans complete with zero extra input. You only intervene when a decision genuinely requires your judgment.

## Core Concepts

### Scan Modes

Every scan operates in a **mode** that determines what happens to the product:

| Mode | What it does |
|------|-------------|
| **Purchase** | Add stock to your inventory (default) |
| **Consume** | Remove stock (you used it) |
| **Consume Spoiled** | Remove spoiled stock |
| **Consume All** | Remove all remaining stock of a product |
| **Open** | Mark a product as opened |
| **Inventory** | Stocktake / inventory correction |
| **Add to Shopping List** | Put it on your Grocy shopping list |
| **Transfer** | Move stock between locations |
| **Provision** | Full product setup: create product, assign barcode, purchase — in one go |

You can switch modes at any time by scanning a **mode barcode** (e.g. scanning `BBUDDY-P` switches to Purchase mode). This mirrors the physical workflow of scanning a printed mode card before scanning products.

### The Scan Queue

The scan queue is a persistent, durable list of barcodes waiting to be processed. It survives Home Assistant restarts and is the backbone of the remote-scanning workflow.

Every barcode that enters through the webhook lands in the queue first. The integration then attempts to **auto-resolve** it immediately — running through the same steps a manual scan would, but filling in all fields with their configured defaults. If the product is known and well-configured, it resolves in under a second with no user input needed.

If auto-resolve can't handle it (unknown product, missing configuration, ambiguous match), the item stays in the queue as **pending** or **failed**, ready for you to process manually.

### Auto-Resolve

Auto-resolve is the hands-free engine behind the queue. When a barcode arrives, it:

1. Looks up the product in Grocy.
2. Checks that the product is properly configured (e.g. best-before days are set, not left at zero).
3. Fills in every form field using its **default** value — the value the product's own configuration says is correct.
4. Submits the transaction to Grocy.

If any step needs a human decision — the product doesn't exist, a required field has no default, or the product config looks suspicious — auto-resolve stops and leaves the item for you.

**What auto-resolves well:**
- Purchasing known products with complete configuration
- Consuming known products
- Any mode where the product is recognized and all fields have defaults

**What needs manual handling:**
- Unknown barcodes (new products)
- Products with incomplete configuration (e.g. best-before days set to 0)
- Recipe production, transfers, and other multi-step workflows

## Getting Started

### Installation

Install via [HACS](https://hacs.xyz) (Home Assistant Community Store) as a custom repository, then add the integration through **Settings → Devices & Services → Add Integration → Grocy Helper**.

### Configuration

You'll need:
- **Grocy API URL** and **API key** — from your Grocy instance (Settings → Manage API keys)
- **Barcode Buddy URL** and **API key** *(optional)* — if you use [Barcode Buddy](https://github.com/Forceu/barcodebuddy) alongside Grocy

Additional options are available under the integration's **Configure** menu:
- Default storage locations (fridge, freezer, recipe output)
- Label printing (enable/disable, auto-print on product creation)
- Form field visibility (price, best-before, shopping location)

## How to Use

### Manual Scanning (Options Flow UI)

Grocy Helper uses Home Assistant's **Options Flow** as its primary user interface. There is no separate dashboard or panel — all interaction happens through the integration's configuration menu, which presents step-by-step forms for scanning, product creation, and queue management.

To start scanning:

1. Go to **Settings → Devices & Services → Grocy Helper → Configure**
2. Choose **Scan barcodes** from the main menu
3. Enter one or more barcodes (comma-separated or one per line) and select a scan mode
4. The integration walks you through each barcode — looking up products, presenting relevant forms, and submitting transactions to Grocy

For known products with good defaults, most scans complete with a single confirmation. For unknown products, you'll be guided through product creation, barcode assignment, and the initial transaction.

### Remote Scanning (Webhook)

The integration registers a **webhook endpoint** with Home Assistant. Any HTTP client, automation, or barcode scanner app can POST barcodes to it.

#### Payload examples

```json
// Single barcode
{ "barcode": "3392590205420" }

// Multiple barcodes in one request
{ "barcodes": ["3392590205420", "7340011492900"] }

// Override the current scan mode
{ "barcode": "3392590205420", "mode": "BBUDDY-P" }

// Structured barcode with embedded metadata
{ "barcode": "<3392590205420|q:2|p:25.0>" }
```

**Structured barcodes** let you embed quantity (`q:`), price (`p:`), and name (`n:`) directly in the barcode string, separated by pipes. Angle brackets are optional.

#### Response

The webhook responds synchronously with per-barcode status:

```json
{
  "status": "ok",
  "results": [
    { "barcode": "3392590205420", "status": "auto_resolved", "item_id": "..." },
    { "barcode": "9999999999999", "status": "queued", "item_id": "..." }
  ]
}
```

| Status | Meaning |
|--------|---------|
| `auto_resolved` | Product was recognized and processed automatically |
| `queued` | Product needs manual processing (check Handle Queue) |
| `failed` | Auto-resolve attempted but hit an error |
| `mode_switched` | Barcode was a mode command, scan mode updated |

#### Dynamic mode switching

Sending a mode barcode through the webhook (e.g. `{"barcode": "BBUDDY-AS"}`) switches the queue's **current mode**. All subsequent barcodes without an explicit `mode` field will use the new mode. This persists across restarts.

This lets you replicate a physical workflow: scan a "Purchase" mode card, then scan a bag of groceries — all via webhook.

### Handle Queue (Manual Recovery)

When auto-resolve can't handle an item, it lands in the queue as pending or failed. To process these manually:

1. Open **Settings → Devices & Services → Grocy Helper → Configure**
2. Choose **Handle Queue** from the main menu
3. Review the summary: pending count, failed count, and a list of each item with its barcode, mode, and status
4. Confirm to process — the integration feeds all items through the normal scan workflow, one by one
5. Each completed item is marked as resolved in the queue

Failed items are automatically retried when you process the queue. If an item fails again, it stays in the queue with an updated error message.

## Automation Ideas

Because scanning works over a webhook, you can trigger it from almost anything:

- **NFC tags** — Stick an NFC tag on a product; tapping it with your phone sends the barcode to the webhook
- **Phone shortcuts** — iOS Shortcuts or Android Tasker can POST to the webhook after scanning a barcode with the camera
- **Barcode scanner apps** — Configure your scanner app's output to POST to the webhook URL
- **Home Assistant automations** — Chain scans with other automations (e.g. scan a product when a smart shelf detects weight change)
- **Voice assistants** — "Hey Google, I used the last milk" → automation sends the milk barcode with Consume mode

## Supported Integrations

| System | Role |
|--------|------|
| [Grocy](https://grocy.info) | Inventory management backend (required) |
| [Barcode Buddy](https://github.com/Forceu/barcodebuddy) | Barcode lookup and mode management (optional, being phased out) |
| [Home Assistant](https://www.home-assistant.io) | UI, automations, webhook host (required) |
| Niimbot label printers | Print labels via Grocy webhook (optional) |

## Credits & Acknowledgments

This project was heavily inspired by [Barcode Buddy](https://github.com/Forceu/barcodebuddy). The initial implementation used Barcode Buddy's simple API endpoints as a shortcut to get up and running quickly with custom scanning workflows on top of Grocy. Many of the core ideas — mode switching via barcodes and the general scan-then-act pattern — originated from or were influenced by Barcode Buddy's design.

The integration is gradually moving away from the Barcode Buddy dependency and implementing Grocy API calls directly, but the debt of inspiration remains. If you're looking for a standalone barcode scanning solution for Grocy (without Home Assistant), Barcode Buddy is an excellent choice.
