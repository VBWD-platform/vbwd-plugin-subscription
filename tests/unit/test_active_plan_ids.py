"""Unit tests for SubscriptionReadModel.active_plan_ids (S49.0).

The read model derives the distinct set of tariff-plan ids the user is
actively entitled to, by reusing the repository's active-subscription list
accessor. The repository is a MagicMock here — no DB.
"""
from unittest.mock import MagicMock
from uuid import uuid4

from plugins.subscription.subscription.services.subscription_read_model import (
    SubscriptionReadModel,
)


def _subscription_with_plan(plan_id):
    subscription = MagicMock()
    subscription.tarif_plan_id = plan_id
    return subscription


def test_active_plan_ids_returns_distinct_plan_ids(monkeypatch):
    user_id = uuid4()
    plan_a = uuid4()
    plan_b = uuid4()

    repository = MagicMock()
    repository.find_active_by_user_list.return_value = [
        _subscription_with_plan(plan_a),
        _subscription_with_plan(plan_b),
        _subscription_with_plan(plan_a),  # duplicate plan -> deduped
    ]

    read_model = SubscriptionReadModel()
    monkeypatch.setattr(read_model, "_subscription_repo", lambda: repository)

    result = read_model.active_plan_ids(user_id)

    assert set(result) == {plan_a, plan_b}
    assert len(result) == 2  # deduped
    repository.find_active_by_user_list.assert_called_once_with(user_id)


def test_active_plan_ids_empty_when_no_active_subscriptions(monkeypatch):
    user_id = uuid4()

    repository = MagicMock()
    repository.find_active_by_user_list.return_value = []

    read_model = SubscriptionReadModel()
    monkeypatch.setattr(read_model, "_subscription_repo", lambda: repository)

    assert read_model.active_plan_ids(user_id) == []
