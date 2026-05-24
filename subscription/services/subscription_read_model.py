"""Subscription read model — implements the core read port.

Relocated verbatim (E2) from the inline subscription reads previously in
core `vbwd/routes/admin/invoices.py` and `vbwd/routes/admin/users.py`.
Same output shape; now plugin-owned so core carries no subscription repo.
"""
from typing import Any, Dict, List
from uuid import UUID

from vbwd.services.subscription_read_model import ISubscriptionReadModel


class SubscriptionReadModel(ISubscriptionReadModel):
    """Read-only subscription projections for core admin surfaces."""

    def _session(self):
        from vbwd.extensions import db

        return db.session

    def _subscription_repo(self):
        from plugins.subscription.subscription.repositories.subscription_repository import (  # noqa: E501
            SubscriptionRepository,
        )

        return SubscriptionRepository(self._session())

    def _tarif_plan_repo(self):
        from plugins.subscription.subscription.repositories.tarif_plan_repository import (  # noqa: E501
            TarifPlanRepository,
        )

        return TarifPlanRepository(self._session())

    def _addon_subscription_repo(self):
        from plugins.subscription.subscription.repositories.addon_subscription_repository import (  # noqa: E501
            AddOnSubscriptionRepository,
        )

        return AddOnSubscriptionRepository(self._session())

    def _invoice_repo(self):
        from vbwd.repositories.invoice_repository import InvoiceRepository

        return InvoiceRepository(self._session())

    def enrich_invoice(self, invoice: Any) -> Dict[str, Any]:
        enrichment: Dict[str, Any] = {}

        if getattr(invoice, "tarif_plan_id", None):
            plan = self._tarif_plan_repo().find_by_id(str(invoice.tarif_plan_id))
            if plan:
                enrichment["plan_name"] = plan.name
                enrichment["plan_description"] = plan.description
                enrichment["plan_billing_period"] = (
                    plan.billing_period.value if plan.billing_period else None
                )
                enrichment["plan_price"] = str(plan.price) if plan.price else None

        if getattr(invoice, "subscription_id", None):
            subscription = self._subscription_repo().find_by_id(
                str(invoice.subscription_id)
            )
            if subscription:
                enrichment["subscription_status"] = (
                    subscription.status.value if subscription.status else None
                )
                enrichment["subscription_start_date"] = (
                    subscription.started_at.isoformat()
                    if subscription.started_at
                    else None
                )
                enrichment["subscription_end_date"] = (
                    subscription.expires_at.isoformat()
                    if subscription.expires_at
                    else None
                )
                enrichment["subscription_is_trial"] = (
                    subscription.trial_end_at is not None
                )
                enrichment["subscription_trial_end"] = (
                    subscription.trial_end_at.isoformat()
                    if subscription.trial_end_at
                    else None
                )

        return enrichment

    def count_user_subscriptions(self, user_id: UUID) -> int:
        return len(self._subscription_repo().find_by_user(user_id))

    def user_addon_subscriptions(self, user_id: UUID) -> List[Dict[str, Any]]:
        addon_sub_repo = self._addon_subscription_repo()
        invoice_repo = self._invoice_repo()

        addon_subs = addon_sub_repo.find_by_user(
            UUID(user_id) if isinstance(user_id, str) else user_id
        )

        result: List[Dict[str, Any]] = []
        for addon_sub in addon_subs:
            data = {
                "id": str(addon_sub.id),
                "addon_name": addon_sub.addon.name if addon_sub.addon else "Unknown",
                "status": addon_sub.status.value,
                "starts_at": addon_sub.starts_at.isoformat()
                if addon_sub.starts_at
                else None,
                "expires_at": addon_sub.expires_at.isoformat()
                if addon_sub.expires_at
                else None,
                "created_at": addon_sub.created_at.isoformat()
                if addon_sub.created_at
                else None,
                "invoice_status": None,
                "first_invoice": None,
                "last_invoice": None,
            }

            if addon_sub.invoice_id:
                invoice = invoice_repo.find_by_id(addon_sub.invoice_id)
                if invoice:
                    invoice_data = {
                        "id": str(invoice.id),
                        "invoice_number": invoice.invoice_number,
                        "created_at": invoice.invoiced_at.isoformat()
                        if invoice.invoiced_at
                        else None,
                    }
                    data["invoice_status"] = invoice.status.value
                    data["first_invoice"] = invoice_data
                    data["last_invoice"] = invoice_data

            result.append(data)

        return result
