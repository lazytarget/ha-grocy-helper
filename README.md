# Grocy Helper for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![GitHub Release](https://img.shields.io/github/v/release/lazytarget/ha-grocy-helper)](https://github.com/lazytarget/ha-grocy-helper/releases)
[![License](https://img.shields.io/github/license/lazytarget/ha-grocy-helper)](LICENSE.md)

> **⚠ Early development — use at your own risk.**
> This project is under active development. Breaking changes are imminent. Configuration schemas, APIs, and behaviors may change between versions without migration paths.

A [Home Assistant](https://www.home-assistant.io) custom integration that extends your [Grocy](https://grocy.info) installation with barcode-driven inventory management — directly from the HA UI.

## What It Does

Grocy Helper turns Home Assistant into a barcode scanning station for Grocy. Scan products to purchase, consume, inventory, transfer, or add to your shopping list — all through Home Assistant's Options Flow interface.

**Key features:**

- **Barcode scanning workflow** — Scan one or many barcodes, and the integration handles product lookup, creation, and the Grocy transaction in a guided step-by-step flow.
- **Webhook endpoint** — POST barcodes from any device, app, or automation. Known products are processed automatically; unknown ones are queued for manual handling.
- **Persistent scan queue** — Barcodes received via webhook are durably queued. Auto-resolve handles what it can; the rest waits in the queue for you.
- **Multiple scan modes** — Purchase, consume, consume spoiled, open, inventory, transfer, shopping list, and a full product provisioning mode.
- **Dynamic mode switching** — Switch modes by scanning a mode barcode, just like a physical barcode scanning station.
- **Structured barcodes** — Embed quantity, price, and name directly in the barcode string.
- **Recipe production** — Consume recipe ingredients and produce output products in a single flow.
- **Label printing** — Trigger Niimbot label printing via Grocy webhooks.

For a full walkthrough, see the [Usage Guide](docs/usage.md).

## Installation

### Via HACS (recommended)

1. Open [HACS](https://hacs.xyz) in Home Assistant
2. Go to **Integrations → ⋮ → Custom repositories**
3. Add `https://github.com/lazytarget/ha-grocy-helper` as an **Integration**
4. Search for "Grocy Helper" in HACS and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration → Grocy Helper**

### Manual

1. Copy the `custom_components/grocy_helper` directory to your Home Assistant `config/custom_components/` folder
2. Restart Home Assistant
3. Add the integration via **Settings → Devices & Services**

## Configuration

During setup you'll be asked for:

| Field | Required | Description |
|-------|----------|-------------|
| Grocy API URL | Yes | Base URL of your Grocy instance |
| Grocy API Key | Yes | API key from Grocy (Settings → Manage API keys) |
| Barcode Buddy URL | No | URL of your Barcode Buddy instance |
| Barcode Buddy API Key | No | Barcode Buddy API key |

After setup, additional options are available under **Configure**:

- Default storage locations (fridge, freezer, recipe output)
- Label printing preferences
- Form field visibility (price, best-before, shopping location)

## Quick Start

1. Go to **Settings → Devices & Services → Grocy Helper → Configure**
2. Choose **Scan barcodes**
3. Enter a barcode and pick a mode
4. Follow the prompts — the integration handles the rest

For remote/automated scanning, POST to the webhook:

```bash
curl -X POST "http://<your-ha>:8123/api/webhook/<webhook_id>" \
  -H "Content-Type: application/json" \
  -d '{"barcode": "3392590205420"}'
```

See the [Usage Guide](docs/usage.md) for webhook payload formats, structured barcodes, and automation ideas.

## Contributing

Issues and pull requests are welcome! That said, please keep in mind that this project is at a **very early stage**. The architecture, APIs, and configuration are still evolving and may change significantly between releases.

**Before contributing:**

- Check [open issues](https://github.com/lazytarget/ha-grocy-helper/issues) to see if your idea or bug is already tracked.
- For larger changes, open an issue first to discuss the approach — it may overlap with planned work or conflict with upcoming refactors.
- Bug reports with steps to reproduce are especially helpful at this stage.

### Development

```bash
# Clone and set up
git clone https://github.com/lazytarget/ha-grocy-helper.git
cd ha-grocy-helper
python -m venv .venv
.venv/Scripts/activate  # or source .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt

# Run tests
pytest tests/ -v
```

## Credits

This project was heavily inspired by [Barcode Buddy](https://github.com/Forceu/barcodebuddy). The initial implementation used Barcode Buddy's API endpoints to bootstrap custom scanning workflows on top of Grocy. Many core ideas — mode switching via barcodes, the scan-then-act pattern — come from Barcode Buddy's design. The integration is gradually moving to direct Grocy API calls, but the debt of inspiration remains.

If you're looking for a standalone barcode scanning solution for Grocy without Home Assistant, [Barcode Buddy](https://github.com/Forceu/barcodebuddy) is an excellent choice.

## License

See [LICENSE.md](LICENSE.md).
