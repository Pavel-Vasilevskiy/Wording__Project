# Cyberpunk Warehouse

Cyberpunk Warehouse is an isolated Flask game module. This iteration installs its data model, mode configuration providers, level-readiness validation, and administrator views. It intentionally has no player gameplay route or Triple Match implementation.

## Module architecture

- `models.py` contains the game-owned SQLAlchemy entities. It shares the portal's `db` and `User` model and does not define authentication or account tables.
- `services/modes.py` provides independent server-side mode validators and idempotently seeds the built-in mode catalog.
- `services/levels.py` checks whether a level has enough valid configuration to be published.
- `routes.py` registers the administrator dashboard, functional Products, Categories, Customers, and Assets sections, read-only Levels and Game Modes catalogs, and explicit planned-section notices. Every route uses the portal's authenticated `User.is_admin` permission and session CSRF token.
- `services/assets.py` validates and normalizes uploaded images, generates unique names, restricts stored paths, and selects persistent storage.
- `static/cyberpunk_warehouse/assets/README.md` describes the logical artwork organization. User files are stored outside the source tree; no artwork is bundled.

The models are registered before Flask `db.create_all()` runs. Existing SQLite databases receive new tables plus additive columns and indexes; no current table is rebuilt. Legacy customers get stable `legacy-customer-<id>` keys during the additive upgrade. Built-in game mode rows are inserted only when missing, so startup does not overwrite admin-managed metadata. `initialize_database()` runs this setup as part of the existing `python app_with_auth.py` startup flow.

## Data model

- `CyberpunkAsset`: stable asset key plus relative storage path/type. Product, customer emotion, level background, and shelf records point to assets rather than embedding paths in gameplay code.
- `CyberpunkProduct`: unique internal key, description, category association (while retaining the original category label for compatibility), rarity, spawn weight, active flag, sort order, and optional image asset.
- `CyberpunkCategory`: unique internal key, description, optional UI icon asset, sort order, active flag, and products.
- `CyberpunkCustomer`: unique internal key, description, customer type, waiting duration, active state, sort order, and optional default/happy/impatient/disappointed artwork references.
- `CyberpunkCustomerArtwork`: normalized customer/state/asset links for extra named states and animation frames. The admin form supports up to three additional named states per customer; adding a state does not require another customer-table column.
- `CyberpunkAsset`: relative filename, asset type, alt text, normalized media type, dimensions, output file size, and active state. Products, categories, customers, and levels keep foreign-key references to reusable records.
- `CyberpunkGameMode`: unique mode key, display information, JSON configuration schema/defaults, and active/built-in flags.
- `CyberpunkLevel`: numbered level, mode FK, difficulty/status, mode configuration, order configuration, optional level timer, customer, background/shelf assets, products, and orders.
- `CyberpunkOrder` and `CyberpunkOrderItem`: per-level order sequence and requested product quantities. Requested quantities are positive whole numbers; the legacy constrained column is retained as a compatibility shadow while the additive `requested_quantity` column stores current values.
- `cyberpunk_level_products`: many-to-many level/product association.

Mode configuration is JSON data stored in the database, not gameplay constants in JavaScript. Mode-specific options live in `CyberpunkLevel.mode_configuration`; `time_limit_seconds` is a level column because it is a commonly queried timed-mode value. Order pacing/configuration is kept separately in `order_configuration`.

## Supported modes

| Key | Purpose | Configuration foundation |
|---|---|---|
| `classic` | Standard objective progression | Non-empty `objective` |
| `timed` | Complete an order before a timer expires | Positive level `time_limit_seconds`; optional lives, failure conditions, scoring rules |
| `unlimited` | No countdown | Timer must be absent; optional move limit and scoring rules |
| `pvp` | Two-player competitive mode | Match duration, synchronization settings, scoring settings, completion conditions |

Each mode has a separate `ModeProvider.validate()` implementation. To add a future mode, create and register a provider in `services/modes.py`, add its catalog definition/default/schema, and add tests. Daily Challenge, Special Challenge, Endless, and Tournament are not seeded or implemented yet.

`level_publish_errors(level)` validates mode settings, warehouse structure, stock quantities, orders, and customer references. `validate_level_for_publish(level)` raises `ValueError` with validation issues. The Level Editor provides Draft, publish/unpublish, archive/restore, duplicate, and administrator preview flows.

## Administrator section

Open `/admin/cyberpunk-warehouse` as a portal administrator. Products, Categories, Customers, Assets, and Levels have create/edit/list/archive/restore workflows; Products and Categories also have safe-delete checks. The level editor uses the existing GameMode provider registry for server-side mode validation. Orders, Game Settings, and Analytics remain planned.

### Level Editor

- Level fields and settings are stored in JSON columns for `warehouse_layout`, `gameplay_configuration`, the active `mode_configuration`, and saved per-mode configurations in `mode_configurations`; `configuration_version` is set to 2 for levels saved by the current editor. Product placement and order lines remain relational references. Switching a level's mode preserves its other mode configuration.
- Layout v2 format: `{version:2, rows, columns, shelves:[{id,name,cells:[{row,column,slots:[product_id|null,product_id|null,product_id|null]}]}]}`. Every cell contains exactly three independent slots. New levels default to a 10×10 grid; dimensions are configurable up to 20×20. Empty slots are allowed in drafts. Three equal non-empty slots receive a visual highlight only; no game action is executed.
- Catalog products can be dragged onto slots to copy them. Slot drag handles move a product; cell Copy/Paste duplicates all three assignments; slots and whole cells can be cleared. Dropdown selection is also available.
- Requested order quantities may be any positive whole number. Publication and order saving compare requested stock against the count of matching warehouse slots. This is a structural stock check and does not simulate whether a player can access/combine those items.
- Legacy v1 layouts are converted in memory when opened. Each legacy group of three stock units is displayed as a triple in one cell, with extra triples assigned to empty cells. The database is updated only when the level is saved. If conversion would exceed the editor grid or lose a non-triple legacy quantity, save is blocked and the original record remains intact.
- SQLite retains the old constrained `quantity` column as a technical shadow for compatibility and stores the actual order amount in the additive `requested_quantity` column. Existing amounts are copied once by the schema upgrader; later application starts do not overwrite them.
- Orders are editable as one customer order per level in this iteration. The `CyberpunkOrder` relationship remains capable of multiple orders for future multi-customer levels.
- Publishing checks structure, active references, customer default artwork, mode configuration, and order-vs-stock counts. It does **not** simulate shelf accessibility, tray occupancy, or the full Triple Match flow; no full solvability claim is made.
- Preview is an administrator-only view of the saved level and does not create gameplay or update player statistics. “Save & Preview” saves before showing it.
- Drafts can be incomplete. Published levels can be unpublished; archived levels restore as Draft. Duplicate creates an independent Draft with copied configuration and relationships.
- Legacy levels receive a generated `legacy-level-<id>` internal key during the additive schema upgrade. No tables are rebuilt or rows removed.

### Artwork uploads and storage

- Supported input: PNG, JPEG, and WebP, up to 5 MB (configurable with `CYBERPUNK_ASSET_MAX_BYTES`). SVG and other formats are rejected.
- Pillow verifies and decodes uploads, applies EXIF orientation, removes metadata, and writes WebP while retaining transparency. Dimensions and normalized output size are stored for display.
- Filenames are random UUIDs; database paths are relative basenames. Uploads preview locally in the browser before submission. Replacing an asset keeps the same database record so existing references continue to use the replacement.
- Local development defaults to `<Flask instance path>/cyberpunk_warehouse/assets`; override with `CYBERPUNK_ASSET_DIR` if desired.
- Production must set `CYBERPUNK_ASSET_DIR` under the persistent `/data` mount. `fly.toml` now configures `/data/cyberpunk_warehouse/assets`, alongside the existing database and avatar paths. No new volume is required. Ensure the current volume is mounted and writable by the app process; the application rejects a production path outside `/data` instead of falling back to ephemeral storage.
- Asset images are served through an administrator-protected route in this iteration. A public read-only delivery policy should be added with gameplay; upload and mutation routes should remain admin-only.

An administrator account is provisioned through the existing `User.is_admin` mechanism documented in `connections/README.md`; no separate account or role system was added.

## Current status and next steps

Implemented: independent models, mode registry and validators, safe table initialization, functional product/category/customer/asset administration, reusable persistent artwork storage, and an admin Level Editor with CRUD, three-slot structured warehouse layouts, arbitrary positive order quantities, validation, preview, duplication and status management.

Next: implement a gameplay-side solvability simulator and multi-order/customer editing, then add player-facing level selection and gameplay/progression/scoring. PvP synchronization and customer timers are later work.
