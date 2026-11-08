# Cyberpunk Warehouse

Cyberpunk Warehouse is an isolated Flask game module. This iteration installs its data model, mode configuration providers, level-readiness validation, and administrator views. It intentionally has no player gameplay route or Triple Match implementation.

## Module architecture

- `models.py` contains the game-owned SQLAlchemy entities. It shares the portal's `db` and `User` model and does not define authentication or account tables.
- `services/modes.py` provides independent server-side mode validators and idempotently seeds the built-in mode catalog.
- `services/levels.py` checks whether a level has enough valid configuration to be published.
- `routes.py` registers the administrator dashboard, read-only Levels and Game Modes catalogs, and explicit planned-section notices. Every route uses the portal's authenticated `User.is_admin` permission.
- `static/cyberpunk_warehouse/assets/README.md` describes the logical artwork folder plan. Artwork and upload endpoints are intentionally absent.

The models are registered before Flask `db.create_all()` runs. Existing SQLite databases receive only new tables; no current table is rebuilt. Built-in game mode rows are inserted only when missing, so startup does not overwrite admin-managed metadata. `initialize_database()` runs this setup as part of the existing `python app_with_auth.py` startup flow.

## Data model

- `CyberpunkAsset`: stable asset key plus relative storage path/type. Product, customer emotion, level background, and shelf records point to assets rather than embedding paths in gameplay code.
- `CyberpunkProduct`: internal key, category, rarity, spawn weight, active flag, optional image asset.
- `CyberpunkCustomer`: customer type, waiting duration, and optional character/happy/impatient/disappointed artwork references.
- `CyberpunkGameMode`: unique mode key, display information, JSON configuration schema/defaults, and active/built-in flags.
- `CyberpunkLevel`: numbered level, mode FK, difficulty/status, mode configuration, order configuration, optional level timer, customer, background/shelf assets, products, and orders.
- `CyberpunkOrder` and `CyberpunkOrderItem`: per-level order sequence and requested product quantities. Quantities are validated in Python and by a database check constraint to be positive multiples of three.
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

`level_publish_errors(level)` validates mode settings and required orders/customer/product links. `validate_level_for_publish(level)` raises `ValueError` with the validation issues. There is no publish/edit UI in this iteration.

## Administrator section

Open `/admin/cyberpunk-warehouse` as a portal administrator. The dashboard and the read-only Levels/Game Modes pages are available. Products, Customers, Orders, Game Settings, Assets, and Analytics have pages explicitly marked as planned and expose no fake editing controls.

An administrator account is provisioned through the existing `User.is_admin` mechanism documented in `connections/README.md`; no separate account or role system was added.

## Current status and next steps

Implemented: independent models, mode registry and validators, order multiple-of-three enforcement, safe table initialization, admin dashboard/catalogs, and module tests.

Next: content editors for Products/Customers/Assets, a level editor that calls server validators before publish, player-facing level selection, then gameplay and its progression/scoring. PvP synchronization and customer timers are later work.
