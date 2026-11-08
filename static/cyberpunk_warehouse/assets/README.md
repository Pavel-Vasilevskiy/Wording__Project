# Cyberpunk Warehouse assets

Artwork is intentionally not included in this foundation iteration. Store replaceable artwork under logical groups such as `products/`, `customers/`, `customer-emotions/`, `warehouse/`, `shelves/`, `ui/`, and `animations/`. Persist a stable `CyberpunkAsset.asset_key` and a project-relative `storage_path` in the database; gameplay and templates should resolve assets through that record instead of embedding filenames. Upload/replace flows and production storage policy will be established with the asset admin iteration.
