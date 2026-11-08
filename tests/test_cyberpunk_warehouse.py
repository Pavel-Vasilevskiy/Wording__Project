import unittest
from pathlib import Path

from flask import g

from app_with_auth import app
from cyberpunk_warehouse.models import (
    CyberpunkAsset,
    CyberpunkCustomer,
    CyberpunkGameMode,
    CyberpunkLevel,
    CyberpunkOrder,
    CyberpunkOrderItem,
    CyberpunkProduct,
)
from cyberpunk_warehouse.services.levels import level_publish_errors, validate_level_for_publish
from cyberpunk_warehouse.services.modes import seed_builtin_game_modes, validate_mode_configuration
from models import User, db


class CyberpunkWarehouseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = Path(__file__).parent / ".cyberpunk-warehouse-test.sqlite"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{cls.database_path}{suffix}").unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            SECRET_KEY="cyberpunk-warehouse-test-secret",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{cls.database_path}",
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
        customer = CyberpunkCustomer(name="Neon Courier", character_artwork=art)
        level = CyberpunkLevel(
            name="Dock 01",
            level_number=1,
            game_mode=mode,
            mode_configuration=configuration if configuration is not None else dict(mode.default_configuration),
            order_configuration={"order_count": 1},
            time_limit_seconds=time_limit,
            customer=customer,
            products=[product],
        )
        order = CyberpunkOrder(level=level, customer=customer, display_order=1, name="Courier order")
        order.items.append(CyberpunkOrderItem(product=product, quantity=3))
        db.session.add(level)
        db.session.commit()
        return level, product, customer

    def test_module_is_registered_and_builtin_modes_seed_idempotently(self):
        self.assertIn("cyberpunk_warehouse_admin", app.blueprints)
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

    def test_order_quantities_must_be_positive_multiples_of_three(self):
        item = CyberpunkOrderItem(order_id=1, product_id=1, quantity=3)
        self.assertEqual(item.quantity, 3)
        for invalid in (0, -3, 1, 4, True):
            with self.subTest(quantity=invalid), self.assertRaises(ValueError):
                item.quantity = invalid

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
        self.assertIn(b"Foundation overview", dashboard.data)
        self.assertIn(b"Game Settings", dashboard.data)
        self.assertIn(b"Planned", dashboard.data)
        self.assertEqual(self.client.get("/admin/cyberpunk-warehouse/levels").status_code, 200)
        modes_page = self.client.get("/admin/cyberpunk-warehouse/game-modes")
        self.assertEqual(modes_page.status_code, 200)
        self.assertIn(b"Unlimited", modes_page.data)
        planned = self.client.get("/admin/cyberpunk-warehouse/sections/assets")
        self.assertEqual(planned.status_code, 200)
        self.assertIn(b"not implemented", planned.data.lower())
        self.assertEqual(self.client.get("/admin/cyberpunk-warehouse/sections/unknown").status_code, 404)


if __name__ == "__main__":
    unittest.main()
