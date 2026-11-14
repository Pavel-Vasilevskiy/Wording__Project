import unittest
import io
from pathlib import Path
from unittest.mock import patch

from flask import g
from PIL import Image

from app_with_auth import app
from cyberpunk_warehouse.models import (
    CyberpunkAsset,
    CyberpunkCategory,
    CyberpunkCustomer,
    CyberpunkCustomerArtwork,
    CyberpunkGameMode,
    CyberpunkGameAction,
    CyberpunkGameSession,
    CyberpunkLevel,
    CyberpunkOrder,
    CyberpunkOrderItem,
    CyberpunkProduct,
)
from cyberpunk_warehouse.services.levels import level_publish_errors, normalize_warehouse_layout, validate_level_for_publish
from cyberpunk_warehouse.services.modes import seed_builtin_game_modes, validate_mode_configuration
from cyberpunk_warehouse.services.assets import AssetError, asset_storage_directory, process_uploaded_image, validate_storage_reference
from cyberpunk_warehouse.services.game_engine import ClassicGameEngine, GameActionError
from models import User, db
from cyberpunk_warehouse.schema import upgrade_cyberpunk_warehouse_schema
from sqlalchemy import create_engine, inspect


class CyberpunkWarehouseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".cyberpunk-warehouse-test.sqlite"
        cls.asset_storage_path = Path(__file__).parent.parent / "static" / "cyberpunk_warehouse" / "assets"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="cyberpunk-warehouse-test-secret",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{cls.database_path}",
            CYBERPUNK_ASSET_DIR=str(cls.asset_storage_path),
            FLASK_ENV="testing",
        )

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        seed_builtin_game_modes()
        self.admin = self.create_user("admin", is_admin=True)
        self.member = self.create_user("member")
        self.client = app.test_client()

    def tearDown(self):
        for asset in CyberpunkAsset.query.all():
            try:
                (self.asset_storage_path / asset.storage_path).unlink(missing_ok=True)
            except OSError:
                pass
        db.session.remove()
        self.context.pop()

    def create_user(self, suffix, is_admin=False):
        user = User(
            google_id=f"cyberpunk-{suffix}",
            email=f"{suffix}@cyberpunk.test",
            name=f"{suffix.title()} Player",
            is_admin=is_admin,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def login(self, user):
        g.pop("_login_user", None)
        with self.client.session_transaction() as saved:
            saved["_user_id"] = str(user.id)
            saved["_fresh"] = True
        g.pop("_login_user", None)

    def create_level_content(self, mode_key="classic", configuration=None, time_limit=None):
        mode = CyberpunkGameMode.query.filter_by(key=mode_key).one()
        art = CyberpunkAsset(asset_key="product:crate", asset_type="product", storage_path="products/crate.webp")
        product = CyberpunkProduct(name="Supply Crate", internal_key="supply-crate", image_asset=art)
        customer = CyberpunkCustomer(name="Neon Courier", internal_key="neon-courier", character_artwork=art)
        db.session.add_all([art, product, customer])
        db.session.flush()
        level = CyberpunkLevel(
            name="Dock 01",
            internal_key="dock-01",
            level_number=1,
            game_mode=mode,
            mode_configuration=configuration if configuration is not None else dict(mode.default_configuration),
            order_configuration={"order_count": 1},
            time_limit_seconds=time_limit,
            customer=customer,
            products=[product],
            warehouse_layout={"version": 1, "rows": 1, "columns": 1, "shelves": [
                {"id": "shelf-1", "name": "Shelf 1", "cells": [
                    {"row": 0, "column": 0, "product_id": product.id, "quantity": 3}
                ]}
            ]},
        )
        order = CyberpunkOrder(level=level, customer=customer, display_order=1, name="Courier order")
        order.items.append(CyberpunkOrderItem(product=product, quantity=3))
        db.session.add(level)
        db.session.commit()
        return level, product, customer

    def test_module_is_registered_and_builtin_modes_seed_idempotently(self):
        self.assertIn("cyberpunk_warehouse_admin", app.blueprints)
        self.assertIn("cyberpunk_warehouse_game", app.blueprints)
        self.assertIn("/cyberpunk-warehouse/", {rule.rule for rule in app.url_map.iter_rules()})
        self.assertIn("/admin/cyberpunk-warehouse/levels", {rule.rule for rule in app.url_map.iter_rules()})
        self.assertEqual({mode.key for mode in CyberpunkGameMode.query.all()}, {"classic", "timed", "unlimited", "pvp"})
        seed_builtin_game_modes()
        self.assertEqual(CyberpunkGameMode.query.count(), 4)

    def test_level_relations_and_published_level_readiness(self):
        level, product, customer = self.create_level_content()
        self.assertEqual(level.products[0].id, product.id)
        self.assertEqual(level.orders[0].customer.id, customer.id)
        self.assertEqual(level.orders[0].items[0].product.id, product.id)
        self.assertEqual(level_publish_errors(level), [])
        self.assertTrue(validate_level_for_publish(level))

    def test_each_mode_has_server_side_configuration_validation(self):
        self.assertEqual(validate_mode_configuration("classic", {"objective": "complete_order"}), [])
        self.assertEqual(validate_mode_configuration("classic", {"objective": "complete_order"}, time_limit_seconds=60), [])
        self.assertTrue(validate_mode_configuration("classic", {}) )
        self.assertTrue(validate_mode_configuration("timed", {"lives": 2}, time_limit_seconds=None))
        self.assertEqual(validate_mode_configuration("timed", {"lives": 2}, time_limit_seconds=90), [])
        self.assertEqual(validate_mode_configuration("unlimited", {"move_limit": None}, time_limit_seconds=None), [])
        self.assertTrue(validate_mode_configuration("unlimited", {}, time_limit_seconds=60))
        pvp_config = {
            "match_duration_seconds": 240,
            "synchronization_settings": {"start_together": True},
            "competitive_scoring": {},
            "completion_conditions": ["all_orders_completed"],
        }
        self.assertEqual(validate_mode_configuration("pvp", pvp_config), [])
        self.assertTrue(validate_mode_configuration("future-mode", {}))

    def test_timed_level_can_be_assigned_without_changing_level_architecture(self):
        level, _product, _customer = self.create_level_content(
            mode_key="timed",
            configuration={"lives": 2, "failure_conditions": ["timer_expired"], "scoring_rules": {}},
            time_limit=75,
        )
        self.assertEqual(level.game_mode.key, "timed")
        self.assertEqual(level.time_limit_seconds, 75)
        self.assertEqual(level_publish_errors(level), [])

    def test_admin_level_editor_saves_incomplete_draft_and_publishes_only_valid_structure(self):
        self.login(self.admin)
        token = self._csrf("/admin/cyberpunk-warehouse/levels/new")
        mode = CyberpunkGameMode.query.filter_by(key="classic").one()
        product_a = CyberpunkProduct(name="Draft Milk", internal_key="draft-milk")
        product_b = CyberpunkProduct(name="Draft Bread", internal_key="draft-bread")
        db.session.add_all([product_a, product_b])
        db.session.flush()
        response = self.client.post("/admin/cyberpunk-warehouse/levels/new", data={
            "csrf_token": token, "action": "save", "name": "Draft Harbor", "internal_key": "draft-harbor",
            "level_number": "20", "sort_order": "5", "difficulty": "normal", "game_mode_id": str(mode.id),
            "layout_rows": "1", "layout_columns": "1", "order_items_count": "2",
            "sorting_tray_capacity": "7", "initial_lives": "3", "base_points": "0",
            f"cell_0_0_slot_0": str(product_a.id), f"cell_0_0_slot_1": str(product_b.id),
        })
        self.assertEqual(response.status_code, 302)
        level = CyberpunkLevel.query.filter_by(internal_key="draft-harbor").one()
        self.assertEqual(level.status, "draft")
        self.assertEqual(level.warehouse_layout["version"], 2)
        self.assertEqual(level.warehouse_layout["shelves"][0]["cells"][0]["slots"], [product_a.id, product_b.id, None])
        edit = self.client.get(f"/admin/cyberpunk-warehouse/levels/{level.id}/edit")
        self.assertEqual(edit.status_code, 200)
        self.assertIn(b"Warehouse layout", edit.data)
        self.assertIn(b"Randomize Warehouse", edit.data)
        self.assertEqual(self.client.get(f"/admin/cyberpunk-warehouse/levels/{level.id}/preview").status_code, 200)
        preview = self.client.post("/admin/cyberpunk-warehouse/levels/preview", data={
            "csrf_token": token, "name": "Unsaved preview", "internal_key": "unsaved-preview",
            "level_number": "21", "difficulty": "easy", "game_mode_id": str(mode.id),
            "layout_rows": "1", "layout_columns": "1", "order_items_count": "0",
        })
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b"unsaved form data", preview.data)
        db.session.rollback()
        self.assertIsNone(CyberpunkLevel.query.filter_by(internal_key="unsaved-preview").first())

    def test_level_duplicate_is_a_separate_draft_and_level_pages_are_admin_only(self):
        level, _, _ = self.create_level_content()
        self.assertEqual(self.client.get(f"/admin/cyberpunk-warehouse/levels/{level.id}/edit").status_code, 302)
        self.login(self.member)
        self.assertEqual(self.client.get(f"/admin/cyberpunk-warehouse/levels/{level.id}/edit").status_code, 403)
        self.login(self.admin)
        token = self._csrf()
        response = self.client.post(f"/admin/cyberpunk-warehouse/levels/{level.id}/duplicate", data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        duplicate = CyberpunkLevel.query.filter(CyberpunkLevel.id != level.id).one()
        self.assertEqual(duplicate.status, "draft")
        self.assertEqual(duplicate.warehouse_layout, level.warehouse_layout)
        self.assertEqual(duplicate.orders[0].items[0].quantity, 3)

    def test_order_quantities_allow_any_positive_whole_number(self):
        for quantity in (1, 2, 3, 8):
            item = CyberpunkOrderItem(order_id=1, product_id=1, quantity=quantity)
            self.assertEqual(item.quantity, quantity)
        item = CyberpunkOrderItem(order_id=1, product_id=1, quantity=3)
        for invalid in (0, -3, True, 1.5):
            with self.subTest(quantity=invalid), self.assertRaises(ValueError):
                item.quantity = invalid

    def test_v2_cells_support_triplets_mixed_slots_and_stock_validation(self):
        level, first, _customer = self.create_level_content()
        second = CyberpunkProduct(name="Boxed Milk", internal_key="boxed-milk")
        third = CyberpunkProduct(name="Neon Cola", internal_key="neon-cola")
        db.session.add_all([second, third])
        db.session.flush()
        cases = ([first.id, first.id, first.id], [first.id, second.id, third.id], [first.id, first.id, second.id])
        for slots in cases:
            with self.subTest(slots=slots):
                level.warehouse_layout = {"version": 2, "rows": 1, "columns": 1, "shelves": [
                    {"id": "shelf-1", "name": "Shelf 1", "cells": [{"row": 0, "column": 0, "slots": list(slots)}]}
                ]}
                level.products = list({p.id: p for p in (first, second, third) if p.id in slots}.values())
                order = level.orders[0]
                order.items.clear()
                # Flush delete-orphan rows before reusing the unique
                # (order_id, product_id) keys in the next test case.
                db.session.flush()
                products_by_id = {product.id: product for product in (first, second, third)}
                for product_id in set(slots):
                    db.session.add(CyberpunkOrderItem(order=order, product=products_by_id[product_id], quantity=slots.count(product_id)))
                db.session.flush()
                errors = level_publish_errors(level)
                if slots[0] == slots[1] == slots[2]:
                    self.assertEqual(errors, [])
                else:
                    self.assertTrue(any("form enough triples" in error for error in errors))

    def test_legacy_layout_conversion_preserves_stock_as_individual_cells(self):
        converted = normalize_warehouse_layout({"version": 1, "rows": 1, "columns": 1, "shelves": [
            {"id": "old", "name": "Shelf", "cells": [{"row": 0, "column": 0, "product_id": 41, "quantity": 6}]}
        ]})
        filled = [slot for shelf in converted["shelves"] for cell in shelf["cells"] for slot in cell["slots"] if slot]
        self.assertEqual(converted["version"], 2)
        self.assertEqual(filled, [41] * 6)
        self.assertEqual(sum(1 for shelf in converted["shelves"] for cell in shelf["cells"] if cell["slots"] == [41,41,41]), 2)

    def test_level_structure_checks_reject_duplicate_cells_and_insufficient_order_stock(self):
        level, _product, _customer = self.create_level_content()
        level.warehouse_layout["shelves"][0]["cells"].append(
            {"row": 0, "column": 0, "product_id": level.products[0].id, "quantity": 3}
        )
        self.assertTrue(any("duplicate cell" in error for error in level_publish_errors(level)))
        level.warehouse_layout = {"version": 1, "rows": 1, "columns": 1, "shelves": [
            {"id": "shelf-1", "name": "Shelf 1", "cells": [
                {"row": 0, "column": 0, "product_id": level.products[0].id, "quantity": 3}
            ]}
        ]}
        level.orders[0].items[0].quantity = 6
        self.assertTrue(any("exceeds warehouse stock" in error for error in level_publish_errors(level)))

    def test_published_v2_layout_may_have_empty_cells_and_slots(self):
        level, product, _customer = self.create_level_content()
        level.warehouse_layout = {"version": 2, "rows": 1, "columns": 3, "shelves": [
            {"id": "shelf-1", "name": "Shelf 1", "cells": [
                {"row": 0, "column": 0, "slots": [product.id, None, None]},
                {"row": 0, "column": 1, "slots": [product.id, None, None]},
                {"row": 0, "column": 2, "slots": [product.id, None, None]},
            ]}
        ]}
        level.orders[0].items[0].quantity = 1
        self.assertEqual(level_publish_errors(level), [])

    def test_published_stock_must_be_gatherable_in_triples_for_order(self):
        level, product, _customer = self.create_level_content()
        level.orders[0].items[0].quantity = 2
        level.warehouse_layout = {"version": 2, "rows": 1, "columns": 1, "shelves": [
            {"id": "shelf-1", "name": "Shelf 1", "cells": [
                {"row": 0, "column": 0, "slots": [product.id, product.id, None]}
            ]}
        ]}
        self.assertTrue(any("form enough triples" in error for error in level_publish_errors(level)))
        level.warehouse_layout["shelves"][0]["cells"][0]["slots"][2] = product.id
        self.assertEqual(level_publish_errors(level), [])

    def test_classic_engine_swaps_and_collects_only_local_triples(self):
        engine = ClassicGameEngine()
        product_id = 11
        state = engine.initial_state({
            "warehouse_layout": {"version": 2, "rows": 1, "columns": 2, "shelves": [
                {"id": "shelf-1", "cells": [
                    {"row": 0, "column": 0, "slots": [product_id, product_id, None]},
                    {"row": 0, "column": 1, "slots": [product_id, None, None]},
                ]}
            ]},
            "orders": [{"items": [{"product_id": product_id, "quantity": 2}]}],
        })
        state = engine.apply(state, {"type": "move", "source": {"row": 0, "column": 1, "slot": 0}, "target": {"row": 0, "column": 0, "slot": 2}})
        self.assertEqual(state["inventory"], {str(product_id): 3})
        self.assertEqual(state["warehouse"]["shelves"][0]["cells"][0]["slots"], [None, None, None])
        self.assertEqual(state["warehouse"]["shelves"][0]["cells"][1]["slots"], [None, None, None])
        with self.assertRaises(GameActionError):
            engine.apply(state, {"type": "deliver", "product_id": product_id, "quantity": 4})

        swapped = engine.initial_state({
            "warehouse_layout": {"version": 2, "rows": 1, "columns": 1, "shelves": [
                {"id": "shelf-1", "cells": [{"row": 0, "column": 0, "slots": [product_id, product_id, 99]}]}
            ]},
            "orders": [{"items": [{"product_id": product_id, "quantity": 1}]}],
        })
        swapped = engine.apply(swapped, {"type": "move", "source": {"row": 0, "column": 0, "slot": 2}, "target": {"row": 0, "column": 0, "slot": 0}})
        self.assertEqual(swapped["warehouse"]["shelves"][0]["cells"][0]["slots"], [99, product_id, product_id])
        self.assertEqual(swapped["inventory"], {})

    def test_classic_engine_does_not_collect_across_cells_and_validates_coordinates(self):
        engine = ClassicGameEngine()
        product_id = 12
        state = engine.initial_state({
            "warehouse_layout": {"version": 2, "rows": 1, "columns": 2, "shelves": [
                {"id": "shelf-1", "cells": [
                    {"row": 0, "column": 0, "slots": [product_id, product_id, None]},
                    {"row": 0, "column": 1, "slots": [product_id, None, None]},
                ]}
            ]},
            "orders": [{"items": [{"product_id": product_id, "quantity": 1}]}],
        })
        state = engine.apply(state, {"type": "move", "source": {"row": 0, "column": 0, "slot": 0}, "target": {"row": 0, "column": 0, "slot": 2}})
        self.assertEqual(state["inventory"], {})
        self.assertEqual(state["warehouse"]["shelves"][0]["cells"][1]["slots"], [product_id, None, None])
        with self.assertRaises(GameActionError):
            engine.apply(state, {"type": "move", "source": {"row": 4, "column": 0, "slot": 0}, "target": {"row": 0, "column": 0, "slot": 0}})

    def test_classic_engine_enforces_configured_deadline_and_pause_freezes_it(self):
        from datetime import datetime, timedelta

        engine = ClassicGameEngine()
        now = datetime(2026, 11, 14, 12, 0, 0)
        snapshot = {
            "warehouse_layout": {"version": 2, "rows": 1, "columns": 1, "shelves": [
                {"id": "shelf-1", "cells": [{"row": 0, "column": 0, "slots": [1, 1, 1]}]}
            ]},
            "time_limit_seconds": 10,
            "gameplay_configuration": {"customer_waiting_time_seconds": 0},
            "orders": [{"customer": {"waiting_seconds": 0}, "items": [{"product_id": 1, "quantity": 3}]}],
        }
        state = engine.initial_state(snapshot, now=now)
        state = engine.apply(state, {"type": "pause"}, now=now + timedelta(seconds=4))
        state = engine.apply(state, {"type": "resume"}, now=now + timedelta(seconds=40))
        state = engine.apply(state, {"type": "tick"}, now=now + timedelta(seconds=45))
        self.assertEqual(state["status"], "active")
        state = engine.apply(state, {"type": "tick"}, now=now + timedelta(seconds=47))
        self.assertEqual(state["status"], "game_over")
        self.assertEqual(state["timers"]["game_over_reason"], "level_time_expired")

    def test_game_routes_persist_server_owned_state_and_replay_actions_idempotently(self):
        level, product, _customer = self.create_level_content()
        level.orders[0].items[0].quantity = 2
        level.status = "published"
        db.session.commit()
        self.login(self.admin)
        self.assertEqual(self.client.get("/cyberpunk-warehouse/").status_code, 200)
        with self.client.session_transaction() as saved:
            csrf = saved["cyberpunk_game_csrf"]
        started = self.client.post(f"/cyberpunk-warehouse/levels/{level.id}/start", data={"csrf_token": csrf})
        self.assertEqual(started.status_code, 302)
        game = CyberpunkGameSession.query.one()
        self.login(self.member)
        self.assertEqual(self.client.get(f"/cyberpunk-warehouse/session/{game.id}").status_code, 404)
        self.login(self.admin)
        self.assertEqual(game.state["level"]["mode"], "classic")
        self.assertEqual(game.state["warehouse"]["version"], 2)
        action = {"action_id": "collect-triple-001", "revision": 0, "action": {
            "type": "move", "source": {"row": 0, "column": 0, "slot": 0},
            "target": {"row": 0, "column": 0, "slot": 1},
        }}
        first = self.client.post(f"/cyberpunk-warehouse/api/session/{game.id}/action", json=action, headers={"X-CSRF-Token": csrf})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["state"]["inventory"], {str(product.id): 3})
        replay = self.client.post(f"/cyberpunk-warehouse/api/session/{game.id}/action", json=action, headers={"X-CSRF-Token": csrf})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json, first.json)
        self.assertEqual(CyberpunkGameAction.query.count(), 1)
        deliver = {"action_id": "deliver-two-0001", "revision": 1, "action": {"type": "deliver", "product_id": product.id, "quantity": 2}}
        result = self.client.post(f"/cyberpunk-warehouse/api/session/{game.id}/action", json=deliver, headers={"X-CSRF-Token": csrf})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["state"]["inventory"], {str(product.id): 1})
        self.assertEqual(result.json["state"]["status"], "completed")
        self.assertEqual(CyberpunkGameSession.query.one().status, "completed")

    def test_level_structure_validator_handles_malformed_layout_types(self):
        level, _, _ = self.create_level_content()
        level.warehouse_layout = {"rows": "many", "columns": [], "shelves": [
            {"id": "bad", "cells": [{"row": 0, "column": 0, "product_id": 1, "quantity": 3}]}
        ]}
        errors = level_publish_errors(level)
        self.assertTrue(any("dimensions" in error for error in errors))

    def test_anonymous_and_regular_users_cannot_access_admin_routes(self):
        paths = [
            "/admin/cyberpunk-warehouse/",
            "/admin/cyberpunk-warehouse/levels",
            "/admin/cyberpunk-warehouse/game-modes",
            "/admin/cyberpunk-warehouse/sections/assets",
        ]
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 302)
        self.login(self.member)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_admin_can_view_dashboard_foundation_and_explicit_planned_sections(self):
        self.login(self.admin)
        dashboard = self.client.get("/admin/cyberpunk-warehouse/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Content management", dashboard.data)
        self.assertIn(b"Game Settings", dashboard.data)
        self.assertIn(b"Planned", dashboard.data)
        self.assertEqual(self.client.get("/admin/cyberpunk-warehouse/levels").status_code, 200)
        modes_page = self.client.get("/admin/cyberpunk-warehouse/game-modes")
        self.assertEqual(modes_page.status_code, 200)
        self.assertIn(b"Unlimited", modes_page.data)
        planned = self.client.get("/admin/cyberpunk-warehouse/sections/analytics")
        self.assertEqual(planned.status_code, 200)
        self.assertIn(b"not implemented", planned.data.lower())
        self.assertEqual(self.client.get("/admin/cyberpunk-warehouse/sections/unknown").status_code, 404)

    def _csrf(self, path="/admin/cyberpunk-warehouse/assets"):
        self.client.get(path)
        with self.client.session_transaction() as saved:
            return saved["cyberpunk_admin_csrf"]

    @staticmethod
    def _image_bytes(image_format="PNG", color=(20, 120, 220, 255)):
        image = Image.new("RGBA", (32, 24), color)
        if image_format == "JPEG":
            image = image.convert("RGB")
        stream = io.BytesIO()
        image.save(stream, format=image_format)
        return stream.getvalue()

    def test_product_create_edit_duplicate_key_archive_restore_and_safe_delete(self):
        self.login(self.admin)
        token = self._csrf("/admin/cyberpunk-warehouse/products/new")
        category = CyberpunkCategory(name="Drinks", internal_key="drinks")
        db.session.add(category)
        db.session.commit()
        response = self.client.post("/admin/cyberpunk-warehouse/products/new", data={
            "csrf_token": token, "name": "Neon Soda", "internal_key": "neon-soda", "category_id": str(category.id),
            "description": "Bright can", "rarity": "rare", "spawn_weight": "7", "sort_order": "2", "is_active": "1",
        }, follow_redirects=True)
        self.assertIn(b"Product created", response.data)
        product = CyberpunkProduct.query.filter_by(internal_key="neon-soda").one()
        self.assertEqual(product.product_category.name, "Drinks")
        response = self.client.post("/admin/cyberpunk-warehouse/products/new", data={
            "csrf_token": token, "name": "Duplicate", "internal_key": "neon-soda", "spawn_weight": "1", "rarity": "common",
        }, follow_redirects=True)
        self.assertIn(b"already in use", response.data)
        self.assertEqual(CyberpunkProduct.query.count(), 1)
        response = self.client.post("/admin/cyberpunk-warehouse/products/new", data={
            "csrf_token": token, "name": "Bad Weight", "internal_key": "bad-weight", "spawn_weight": "0", "rarity": "common",
        }, follow_redirects=True)
        self.assertIn(b"at least 1", response.data)
        self.assertEqual(CyberpunkProduct.query.count(), 1)
        response = self.client.post(f"/admin/cyberpunk-warehouse/products/{product.id}/edit", data={
            "csrf_token": token, "name": "Neon Soda XL", "internal_key": "neon-soda", "category_id": str(category.id),
            "rarity": "epic", "spawn_weight": "3", "sort_order": "4", "is_active": "1",
        }, follow_redirects=True)
        self.assertIn(b"Product updated", response.data)
        self.assertEqual(db.session.get(CyberpunkProduct, product.id).name, "Neon Soda XL")
        self.client.post(f"/admin/cyberpunk-warehouse/products/{product.id}/status", data={"csrf_token": token, "is_active": "0"})
        self.assertFalse(db.session.get(CyberpunkProduct, product.id).is_active)
        self.client.post(f"/admin/cyberpunk-warehouse/products/{product.id}/status", data={"csrf_token": token, "is_active": "1"})
        self.assertTrue(db.session.get(CyberpunkProduct, product.id).is_active)
        self.client.post(f"/admin/cyberpunk-warehouse/products/{product.id}/delete", data={"csrf_token": token})
        self.assertIsNone(db.session.get(CyberpunkProduct, product.id))

    def test_product_with_level_or_order_references_is_only_archived(self):
        self.login(self.admin)
        token = self._csrf("/admin/cyberpunk-warehouse/products/new")
        level, product, _customer = self.create_level_content()
        response = self.client.post(f"/admin/cyberpunk-warehouse/products/{product.id}/delete", data={"csrf_token": token}, follow_redirects=True)
        self.assertIn(b"Archive it instead", response.data)
        self.assertIsNotNone(db.session.get(CyberpunkProduct, product.id))
        self.assertEqual(level.products[0].id, product.id)

    def test_categories_assign_products_count_and_prevent_unsafe_delete(self):
        self.login(self.admin)
        token = self._csrf("/admin/cyberpunk-warehouse/categories/new")
        response = self.client.post("/admin/cyberpunk-warehouse/categories/new", data={
            "csrf_token": token, "name": "Snacks", "internal_key": "snacks", "description": "Food", "sort_order": "3", "is_active": "1",
        }, follow_redirects=True)
        self.assertIn(b"Category created", response.data)
        category = CyberpunkCategory.query.filter_by(internal_key="snacks").one()
        product = CyberpunkProduct(name="Chips", internal_key="chips", product_category=category)
        db.session.add(product)
        db.session.commit()
        listing = self.client.get("/admin/cyberpunk-warehouse/categories")
        self.assertIn(b"Snacks", listing.data)
        self.assertIn(b">1<", listing.data)
        response = self.client.post(f"/admin/cyberpunk-warehouse/categories/{category.id}/edit", data={
            "csrf_token": token, "name": "Salty Snacks", "internal_key": "snacks", "description": "Updated", "sort_order": "1", "is_active": "1",
        }, follow_redirects=True)
        self.assertIn(b"Category updated", response.data)
        self.assertEqual(db.session.get(CyberpunkCategory, category.id).sort_order, 1)
        response = self.client.post(f"/admin/cyberpunk-warehouse/categories/{category.id}/delete", data={"csrf_token": token}, follow_redirects=True)
        self.assertIn(b"Reassign or remove", response.data)
        self.assertIsNotNone(db.session.get(CyberpunkCategory, category.id))

    def test_customer_activation_requires_default_art_and_customer_can_be_archived(self):
        self.login(self.admin)
        token = self._csrf("/admin/cyberpunk-warehouse/customers/new")
        response = self.client.post("/admin/cyberpunk-warehouse/customers/new", data={
            "csrf_token": token, "name": "Courier", "internal_key": "courier", "customer_type": "courier",
            "default_waiting_seconds": "30", "sort_order": "0", "is_active": "1",
        }, follow_redirects=True)
        self.assertIn(b"Default customer artwork is required", response.data)
        self.assertEqual(CyberpunkCustomer.query.count(), 0)
        uploaded = self.client.post("/admin/cyberpunk-warehouse/assets", data={
            "csrf_token": token, "asset_type": "customer", "alt_text": "Courier default",
            "image": (io.BytesIO(self._image_bytes()), "courier.png", "image/png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Artwork uploaded", uploaded.data)
        default_art = CyberpunkAsset.query.filter_by(asset_type="customer").one()
        emotion_upload = self.client.post("/admin/cyberpunk-warehouse/assets", data={
            "csrf_token": token, "asset_type": "customer_emotion", "alt_text": "Celebrating courier",
            "image": (io.BytesIO(self._image_bytes("WEBP")), "celebrating.webp", "image/webp"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Artwork uploaded", emotion_upload.data)
        celebration_art = CyberpunkAsset.query.filter_by(asset_type="customer_emotion").one()
        response = self.client.post("/admin/cyberpunk-warehouse/customers/new", data={
            "csrf_token": token, "name": "Courier", "internal_key": "courier", "customer_type": "courier",
            "default_waiting_seconds": "30", "sort_order": "0", "is_active": "1", "character_artwork_id": str(default_art.id),
            "extra_state_key_0": "celebrating", "extra_state_asset_0": str(celebration_art.id),
        }, follow_redirects=True)
        self.assertIn(b"Customer created", response.data)
        customer = CyberpunkCustomer.query.filter_by(internal_key="courier").one()
        self.assertEqual(customer.character_artwork.id, default_art.id)
        self.assertEqual(customer.artwork_states[0].state_key, "celebrating")
        self.assertEqual(customer.artwork_states[0].asset.id, celebration_art.id)
        self.client.post(f"/admin/cyberpunk-warehouse/customers/{customer.id}/status", data={"csrf_token": token, "is_active": "0"})
        self.assertFalse(db.session.get(CyberpunkCustomer, customer.id).is_active)
        self.client.post(f"/admin/cyberpunk-warehouse/customers/{customer.id}/status", data={"csrf_token": token, "is_active": "1"})
        self.assertTrue(db.session.get(CyberpunkCustomer, customer.id).is_active)
        response = self.client.post(f"/admin/cyberpunk-warehouse/customers/{customer.id}/edit", data={
            "csrf_token": token, "name": "Courier", "internal_key": "courier", "customer_type": "courier",
            "default_waiting_seconds": "-5", "is_active": "1", "character_artwork_id": str(default_art.id),
        }, follow_redirects=True)
        self.assertIn(b"at least 0", response.data)
        self.assertEqual(db.session.get(CyberpunkCustomer, customer.id).default_waiting_seconds, 30)
        unused_customer_id = customer.id
        self.client.post(f"/admin/cyberpunk-warehouse/customers/{unused_customer_id}/delete", data={"csrf_token": token})
        self.assertIsNone(db.session.get(CyberpunkCustomer, unused_customer_id))
        _level, _product, referenced_customer = self.create_level_content()
        protected = self.client.post(f"/admin/cyberpunk-warehouse/customers/{referenced_customer.id}/delete", data={"csrf_token": token}, follow_redirects=True)
        self.assertIn(b"Archive it instead", protected.data)
        self.assertIsNotNone(db.session.get(CyberpunkCustomer, referenced_customer.id))

    def test_asset_upload_formats_replace_and_serve_normalized_webp(self):
        self.login(self.admin)
        token = self._csrf()
        for extension, fmt, mime in (("png", "PNG", "image/png"), ("jpg", "JPEG", "image/jpeg"), ("webp", "WEBP", "image/webp")):
            response = self.client.post("/admin/cyberpunk-warehouse/assets", data={
                "csrf_token": token, "asset_type": "product", "alt_text": f"{fmt} item",
                "image": (io.BytesIO(self._image_bytes(fmt)), f"unsafe ../name.{extension}", mime),
            }, content_type="multipart/form-data", follow_redirects=True)
            self.assertIn(b"Artwork uploaded", response.data)
        self.assertEqual(CyberpunkAsset.query.count(), 3)
        asset = CyberpunkAsset.query.order_by(CyberpunkAsset.id).first()
        old_path = self.asset_storage_path / asset.storage_path
        self.assertTrue(old_path.is_file())
        self.assertRegex(asset.storage_path, r"^[0-9a-f]{32}\.webp$")
        self.assertEqual(asset.media_type, "image/webp")
        self.assertEqual((asset.width, asset.height), (32, 24))
        self.assertGreater(asset.file_size, 0)
        served = self.client.get(f"/admin/cyberpunk-warehouse/assets/{asset.id}/image")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.mimetype, "image/webp")
        served.close()
        response = self.client.post(f"/admin/cyberpunk-warehouse/assets/{asset.id}/replace", data={
            "csrf_token": token, "image": (io.BytesIO(self._image_bytes("PNG", (240, 30, 80, 255))), "new.png", "image/png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Artwork replaced", response.data)
        db.session.refresh(asset)
        self.assertNotEqual(asset.storage_path, old_path.name)
        self.assertFalse(old_path.exists())
        self.assertTrue((self.asset_storage_path / asset.storage_path).is_file())

    def test_asset_upload_rejects_unsupported_oversized_and_mismatched_files(self):
        self.login(self.admin)
        token = self._csrf()
        cases = [
            ((b"<svg></svg>", "bad.svg", "image/svg+xml"), b"Unsupported file format"),
            ((b"not-an-image", "bad.png", "image/png"), b"Unable to process"),
            ((self._image_bytes(), "large.png", "image/png"), b"Image must be 5 MB"),
            ((self._image_bytes(), "wrong.jpg", "image/jpeg"), b"extension does not match"),
        ]
        cases[2] = ((b"x" * (5 * 1024 * 1024 + 1024), "large.png", "image/png"), b"Image must be 5 MB")
        for file_data, expected in cases:
            response = self.client.post("/admin/cyberpunk-warehouse/assets", data={
                "csrf_token": token, "asset_type": "product", "image": (io.BytesIO(file_data[0]), file_data[1], file_data[2]),
            }, content_type="multipart/form-data", follow_redirects=True)
            with self.subTest(filename=file_data[1]):
                self.assertIn(expected, response.data)
        self.assertEqual(CyberpunkAsset.query.count(), 0)

    def test_asset_delete_is_blocked_while_referenced_and_csrf_is_required(self):
        self.login(self.admin)
        token = self._csrf()
        content, (width, height) = process_uploaded_image(type("Upload", (), {
            "filename": "art.png", "mimetype": "image/png", "stream": io.BytesIO(self._image_bytes())
        })())
        storage_dir = asset_storage_directory(app.config)
        from cyberpunk_warehouse.services.assets import save_asset_bytes
        filename = save_asset_bytes(storage_dir, content)
        asset = CyberpunkAsset(asset_key="product:test", asset_type="product", storage_path=filename, width=width, height=height)
        product = CyberpunkProduct(name="Referenced", internal_key="referenced", image_asset=asset)
        db.session.add(product)
        db.session.commit()
        response = self.client.post(f"/admin/cyberpunk-warehouse/assets/{asset.id}/delete", data={"csrf_token": token}, follow_redirects=True)
        self.assertIn(b"still referenced", response.data)
        self.assertIsNotNone(db.session.get(CyberpunkAsset, asset.id))
        bad_csrf = self.client.post(f"/admin/cyberpunk-warehouse/assets/{asset.id}/delete", data={"csrf_token": "invalid"})
        self.assertEqual(bad_csrf.status_code, 400)

    def test_asset_storage_rejects_path_traversal_and_requires_persistent_production_path(self):
        with self.assertRaises(AssetError):
            validate_storage_reference("../outside.webp")
        with self.assertRaises(AssetError):
            validate_storage_reference("C:\\outside.webp")
        with self.assertRaises(AssetError):
            validate_storage_reference("folder/asset.webp")
        with self.assertRaises(AssetError):
            validate_storage_reference("invalid\x00name.webp")
        with self.assertRaises(RuntimeError):
            asset_storage_directory({"FLASK_ENV": "production", "CYBERPUNK_ASSET_DIR": "C:/ephemeral/assets"})
        with self.assertRaises(RuntimeError):
            asset_storage_directory({"FLASK_ENV": "production", "CYBERPUNK_ASSET_DIR": "/data/../app/assets"})
        with patch("cyberpunk_warehouse.services.assets.Path.mkdir"):
            production_root = asset_storage_directory({"FLASK_ENV": "production", "CYBERPUNK_ASSET_DIR": "/data/cyberpunk_warehouse/test-assets"})
        self.assertIn("cyberpunk_warehouse", str(production_root))

    def test_admin_catalog_pages_are_restricted_to_admins(self):
        self.login(self.admin)
        token = self._csrf("/admin/cyberpunk-warehouse/products/new")
        paths = ["/products", "/categories", "/customers", "/assets"]
        for path in paths:
            self.assertEqual(self.client.get("/admin/cyberpunk-warehouse" + path).status_code, 200)
        self.login(self.member)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get("/admin/cyberpunk-warehouse" + path).status_code, 403)
        self.assertEqual(self.client.post("/admin/cyberpunk-warehouse/categories/new", data={"csrf_token": token}).status_code, 403)

    def test_additive_schema_upgrade_preserves_existing_catalog_data(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE cyberpunk_warehouse_asset (id INTEGER PRIMARY KEY, asset_key VARCHAR(100) NOT NULL UNIQUE, asset_type VARCHAR(32) NOT NULL, storage_path VARCHAR(255) NOT NULL, media_type VARCHAR(80) NOT NULL, alt_text VARCHAR(180) NOT NULL, is_active BOOLEAN NOT NULL, created_at DATETIME, created_by INTEGER)")
            connection.exec_driver_sql("CREATE TABLE cyberpunk_warehouse_category (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)")
            connection.exec_driver_sql("CREATE TABLE cyberpunk_warehouse_product (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, internal_key VARCHAR(80) NOT NULL, category VARCHAR(80) NOT NULL, image_asset_id INTEGER, rarity VARCHAR(16) NOT NULL, spawn_weight INTEGER NOT NULL, is_active BOOLEAN NOT NULL, created_at DATETIME, updated_at DATETIME)")
            connection.exec_driver_sql("CREATE TABLE cyberpunk_warehouse_customer (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, customer_type VARCHAR(40) NOT NULL, character_artwork_id INTEGER, happy_artwork_id INTEGER, impatient_artwork_id INTEGER, disappointed_artwork_id INTEGER, default_waiting_seconds INTEGER NOT NULL, is_active BOOLEAN NOT NULL, created_at DATETIME, updated_at DATETIME)")
            connection.exec_driver_sql("INSERT INTO cyberpunk_warehouse_customer (id,name,customer_type,default_waiting_seconds,is_active) VALUES (7,'Legacy A','standard',60,1),(8,'Legacy B','robot',30,1)")
            connection.exec_driver_sql("INSERT INTO cyberpunk_warehouse_product (id,name,internal_key,category,rarity,spawn_weight,is_active) VALUES (4,'Old product','old-product','legacy','common',1,1)")
            connection.exec_driver_sql("CREATE TABLE cyberpunk_warehouse_order_item (id INTEGER PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity > 0 AND quantity % 3 = 0))")
            connection.exec_driver_sql("INSERT INTO cyberpunk_warehouse_order_item (id,quantity) VALUES (1,6)")
        upgrade_cyberpunk_warehouse_schema(engine)
        with engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("SELECT internal_key FROM cyberpunk_warehouse_customer ORDER BY id").scalars().all(), ["legacy-customer-7", "legacy-customer-8"])
            self.assertEqual(connection.exec_driver_sql("SELECT description, sort_order FROM cyberpunk_warehouse_product WHERE id=4").one(), ("", 0))
            self.assertIn("category_id", {column["name"] for column in inspect(connection).get_columns("cyberpunk_warehouse_product")})
            self.assertEqual(connection.exec_driver_sql("SELECT name FROM cyberpunk_warehouse_product WHERE id=4").scalar_one(), "Old product")
            self.assertEqual(connection.exec_driver_sql("SELECT requested_quantity FROM cyberpunk_warehouse_order_item WHERE id=1").scalar_one(), 6)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
