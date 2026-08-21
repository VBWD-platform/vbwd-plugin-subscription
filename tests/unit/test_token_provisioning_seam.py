"""Unit tests for the provisioning-guard token seam (S138.0).

``debit_operator_tokens`` must route through ``TokenService.debit_tokens`` with
``commit=False`` so the debit composes into the caller's open transaction (core
commits the whole user-provisioning unit, or rolls it back) instead of mutating
the balance row and building a ``TokenTransaction`` by hand.
"""
from unittest.mock import MagicMock, patch
from uuid import uuid4

from plugins.subscription.subscription.services import token_provisioning


def test_debit_operator_tokens_calls_debit_tokens_with_commit_false():
    session = MagicMock()
    acting_user_id = uuid4()
    fake_service = MagicMock()

    with patch.object(
        token_provisioning, "_build_token_service", return_value=fake_service
    ) as build:
        token_provisioning.debit_operator_tokens(
            session, str(acting_user_id), amount=10, description="Create admin"
        )

    build.assert_called_once_with(session)
    call = fake_service.debit_tokens.call_args
    assert call.kwargs["user_id"] == acting_user_id
    assert call.kwargs["amount"] == 10
    assert call.kwargs["description"] == "Create admin"
    assert call.kwargs["commit"] is False


def test_read_operator_balance_uses_the_same_builder():
    session = MagicMock()
    acting_user_id = uuid4()
    fake_service = MagicMock()
    fake_service.get_balance.return_value = 42

    with patch.object(
        token_provisioning, "_build_token_service", return_value=fake_service
    ) as build:
        result = token_provisioning.read_operator_balance(session, str(acting_user_id))

    build.assert_called_once_with(session)
    fake_service.get_balance.assert_called_once_with(acting_user_id)
    assert result == 42
