"""Unit tests for ``PlanFeatureAccessLevelService.parse_access_level_slugs``.

The fe-admin Features parser stores a single ``access_levels: premium, vip``
line as ``{"access_levels": "premium, vip"}`` (a scalar STRING, because the
colon branch coerces the value to a string). Some plans instead carry a list
value; plain marketing-bullet plans carry a list of strings (no dict) or other
dict keys. The parser must extract the declared access-level slugs from the
string/list forms and ignore everything else.
"""
from plugins.subscription.subscription.services.plan_feature_access_level_service import (  # noqa: E501
    PlanFeatureAccessLevelService,
)


parse = PlanFeatureAccessLevelService.parse_access_level_slugs


class TestParseAccessLevelSlugs:
    def test_dict_with_comma_space_string(self):
        assert parse({"access_levels": "premium, vip"}) == ["premium", "vip"]

    def test_dict_with_comma_only_string(self):
        assert parse({"access_levels": "premium,vip"}) == ["premium", "vip"]

    def test_dict_with_whitespace_only_string(self):
        assert parse({"access_levels": "premium   vip"}) == ["premium", "vip"]

    def test_dict_with_mixed_comma_and_whitespace(self):
        assert parse({"access_levels": " premium ,  vip , "}) == ["premium", "vip"]

    def test_dict_with_list_value(self):
        assert parse({"access_levels": ["premium", "vip"]}) == ["premium", "vip"]

    def test_dict_with_tuple_value(self):
        assert parse({"access_levels": ("premium", "vip")}) == ["premium", "vip"]

    def test_deduplicates_order_stable(self):
        assert parse({"access_levels": "vip, premium, vip, premium"}) == [
            "vip",
            "premium",
        ]

    def test_missing_key_returns_empty(self):
        assert parse({"other": "x"}) == []

    def test_empty_string_value_returns_empty(self):
        assert parse({"access_levels": ""}) == []

    def test_whitespace_only_value_returns_empty(self):
        assert parse({"access_levels": "   "}) == []

    def test_empty_list_value_returns_empty(self):
        assert parse({"access_levels": []}) == []

    def test_non_dict_marketing_bullet_list_returns_empty(self):
        assert parse(["Unlimited storage", "Priority support"]) == []

    def test_none_returns_empty(self):
        assert parse(None) == []

    def test_string_features_returns_empty(self):
        assert parse("access_levels: premium") == []
