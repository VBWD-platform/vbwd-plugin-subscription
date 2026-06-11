"""Integration: bot storefront draft round-trip + public resolution (S53.0).

Drives the real ``BotStorefrontService`` + ``BotCheckoutDraftRepository`` against
the ``db`` fixture and resolves the minted token through the public endpoint
``GET /api/v1/subscription/public/checkout-draft/<token>``:

* accumulate a selection (plan + add-on + token bundle) → ``/checkout`` mints a
  one-time TTL token → the public endpoint returns line items whose prices are
  **recomputed from the live catalogs**;
* an expired token → 404;
* an already-redeemed token → 404 (single-use);
* the linked-vs-unlinked balance read (a plugin→core read).

Catalog rows are created through the models (the plugin's own persistence) —
never raw SQL.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, LineItemType
from vbwd.models.token_bundle import TokenBundle
from vbwd.models.user import User
from vbwd.models.user_token_balance import UserTokenBalance
from vbwd.models.enums import UserRole, UserStatus

from plugins.subscription.subscription.models import AddOn, TarifPlan
from plugins.subscription.subscription.repositories.bot_checkout_draft_repository import (  # noqa: E501
    BotCheckoutDraftRepository,
)
from plugins.subscription.subscription.repositories.addon_repository import (
    AddOnRepository,
)
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from plugins.subscription.subscription.services.bot_storefront_service import (
    BotStorefrontService,
    DraftResolutionError,
)
from vbwd.repositories.token_bundle_repository import TokenBundleRepository
from vbwd.repositories.token_repository import TokenBalanceRepository

PROVIDER = "telegram"
CHAT = "chat-7"
TTL_SECONDS = 900


def _make_plan(db, name="Pro", price="9.99"):
    plan = TarifPlan(
        id=uuid4(),
        name=name,
        slug=f"{name.lower()}-{uuid4().hex[:8]}",
        price_float=float(price),
        price=Decimal(price),
        currency="EUR",
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _make_addon(db, name="Extra", price="2.50"):
    addon = AddOn(
        id=uuid4(),
        name=name,
        slug=f"{name.lower()}-{uuid4().hex[:8]}",
        price=Decimal(price),
        currency="EUR",
        billing_period=BillingPeriod.MONTHLY.value,
        is_active=True,
    )
    db.session.add(addon)
    db.session.commit()
    return addon


def _make_bundle(db, name="100 tokens", price="5.00", tokens=100):
    bundle = TokenBundle(
        id=uuid4(),
        name=name,
        token_amount=tokens,
        price=Decimal(price),
        is_active=True,
    )
    db.session.add(bundle)
    db.session.commit()
    return bundle


def _service(db, clock=None):
    return BotStorefrontService(
        BotCheckoutDraftRepository(db.session),
        checkout_draft_ttl_seconds=TTL_SECONDS,
        clock=clock,
    )


def _catalog_lookups(db):
    plan_repo = TarifPlanRepository(db.session)
    addon_repo = AddOnRepository(db.session)
    bundle_repo = TokenBundleRepository(db.session)

    from uuid import UUID

    def as_uuid(raw):
        return UUID(str(raw))

    return {
        "plan_lookup": lambda item_id: plan_repo.find_by_id(as_uuid(item_id)),
        "addon_lookup": lambda item_id: addon_repo.find_by_id(as_uuid(item_id)),
        "bundle_lookup": lambda item_id: bundle_repo.find_by_id(as_uuid(item_id)),
    }


class TestStorefrontRoundTrip:
    def test_accumulate_checkout_resolve_recomputes_from_catalog(self, db):
        plan = _make_plan(db, name="Pro", price="9.99")
        addon = _make_addon(db, name="Extra", price="2.50")
        bundle = _make_bundle(db, name="100 tokens", price="5.00")

        service = _service(db)
        service.set_plan(PROVIDER, CHAT, str(plan.id))
        service.toggle_addon(PROVIDER, CHAT, str(addon.id))
        service.toggle_token_bundle(PROVIDER, CHAT, str(bundle.id))

        token = service.mint_checkout_token(PROVIDER, CHAT)
        assert token

        resolved = service.resolve_token(token, **_catalog_lookups(db))

        by_type = {item["item_type"]: item for item in resolved}
        assert by_type[LineItemType.SUBSCRIPTION.value]["item_id"] == str(plan.id)
        assert by_type[LineItemType.SUBSCRIPTION.value]["unit_price"] == "9.99"
        assert by_type[LineItemType.ADD_ON.value]["unit_price"] == "2.50"
        assert by_type[LineItemType.TOKEN_BUNDLE.value]["name"] == "100 tokens"

    def test_public_endpoint_returns_recomputed_line_items(self, db, client):
        plan = _make_plan(db, name="Pro", price="9.99")
        service = _service(db)
        service.set_plan(PROVIDER, CHAT, str(plan.id))
        token = service.mint_checkout_token(PROVIDER, CHAT)

        response = client.get(f"/api/v1/subscription/public/checkout-draft/{token}")

        assert response.status_code == 200, response.get_json()
        line_items = response.get_json()["line_items"]
        assert len(line_items) == 1
        assert line_items[0]["item_id"] == str(plan.id)
        assert line_items[0]["unit_price"] == "9.99"
        assert line_items[0]["name"] == "Pro"


class TestTokenSecurity:
    def test_expired_token_resolution_raises(self, db):
        plan = _make_plan(db)
        now = {"value": datetime(2026, 6, 10, 12, 0, 0)}
        service = _service(db, clock=lambda: now["value"])
        service.set_plan(PROVIDER, CHAT, str(plan.id))
        token = service.mint_checkout_token(PROVIDER, CHAT)

        now["value"] = now["value"] + timedelta(seconds=TTL_SECONDS + 1)

        try:
            service.resolve_token(token, **_catalog_lookups(db))
            raised = False
        except DraftResolutionError:
            raised = True
        assert raised

    def test_expired_token_public_endpoint_404(self, db, client):
        plan = _make_plan(db)
        repo = BotCheckoutDraftRepository(db.session)
        service = _service(db)
        service.set_plan(PROVIDER, CHAT, str(plan.id))
        token = service.mint_checkout_token(PROVIDER, CHAT)

        # Force the token already expired in the past.
        draft = repo.find_by_token(token)
        draft.expires_at = datetime.utcnow() - timedelta(seconds=1)
        repo.save(draft)

        response = client.get(f"/api/v1/subscription/public/checkout-draft/{token}")
        assert response.status_code == 404

    def test_already_redeemed_token_public_endpoint_404(self, db, client):
        plan = _make_plan(db)
        service = _service(db)
        service.set_plan(PROVIDER, CHAT, str(plan.id))
        token = service.mint_checkout_token(PROVIDER, CHAT)

        first = client.get(f"/api/v1/subscription/public/checkout-draft/{token}")
        assert first.status_code == 200

        second = client.get(f"/api/v1/subscription/public/checkout-draft/{token}")
        assert second.status_code == 404

    def test_unknown_token_public_endpoint_404(self, db, client):
        response = client.get(
            "/api/v1/subscription/public/checkout-draft/does-not-exist"
        )
        assert response.status_code == 404


class TestBalanceRead:
    def _make_user_with_balance(self, db, balance):
        user = User(
            id=uuid4(),
            email=f"u-{uuid4().hex[:8]}@example.com",
            password_hash="x",
            status=UserStatus.ACTIVE,
            role=UserRole.USER,
        )
        db.session.add(user)
        db.session.commit()
        token_balance = UserTokenBalance(id=uuid4(), user_id=user.id, balance=balance)
        db.session.add(token_balance)
        db.session.commit()
        return user

    def test_balance_read_for_linked_user(self, db):
        user = self._make_user_with_balance(db, 250)
        balance = TokenBalanceRepository(db.session).find_by_user_id(user.id)
        assert balance.balance == 250

    def test_no_balance_row_means_zero(self, db):
        missing_user_id = uuid4()
        balance = TokenBalanceRepository(db.session).find_by_user_id(missing_user_id)
        assert balance is None
