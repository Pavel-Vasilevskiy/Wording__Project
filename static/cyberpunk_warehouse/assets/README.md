# Cyberpunk Warehouse assets

This source-controlled directory is reserved for versioned, non-user-specific artwork. Administrator uploads are stored outside the source tree under `CYBERPUNK_ASSET_DIR` and referenced by the relative `CyberpunkAsset.storage_path` filename. In production that directory must remain under the mounted `/data` volume.

Upload paths use generated UUID WebP names. The original filename is never used as a storage path. Asset types describe products, customers, customer emotions, warehouse backgrounds, shelves, UI/category icons, and future animation frames.
